from flask import Flask, Blueprint, request
import sys
import os

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__))))
from EnmodalSessions import *

from lzstring import LZString
import json

import psycopg2
import psycopg2.extras

import ConfigParser
config = ConfigParser.RawConfigParser()
config.read(os.path.abspath(os.path.join(os.path.dirname(__file__), 'settings.cfg')))

SESSIONS_HOST = config.get('sessions', 'host')
SESSIONS_PORT = config.get('sessions', 'port')
SESSIONS_DBNAME = config.get('sessions', 'dbname')
SESSIONS_USER = config.get('sessions', 'user')
SESSIONS_PASSWORD = config.get('sessions', 'password')
SESSIONS_CONN_STRING = "host='"+SESSIONS_HOST+"' port='"+SESSIONS_PORT+"' dbname='"+SESSIONS_DBNAME+"' user='"+SESSIONS_USER+"' password='"+SESSIONS_PASSWORD+"'"
SESSIONS_SECRET_KEY_PUBLIC = int(config.get('sessions', 'secret_key_public'), 16)
SESSIONS_SECRET_KEY_PRIVATE = int(config.get('sessions', 'secret_key_private'), 16)
SESSION_EXPIRATION_TIME = int(config.get('sessions', 'expiration_time'))

enmodal_map = Blueprint('enmodal_map', __name__)

def save_session(s, user_id, take_snapshot):
    sid = s.sid
    print "saving with sid "+str(sid)
    sdata = str(s.map.to_json())
    sdt = datetime.datetime.now()

    conn = psycopg2.connect(SESSIONS_CONN_STRING)
    cursor = conn.cursor()
    cursor.execute("SELECT id FROM sessions WHERE id = '%s' LIMIT 1", [sid])
    if (cursor.rowcount > 0):
        cursor.execute("UPDATE sessions SET data = %s, updated = %s WHERE id = %s", (sdata, sdt, sid))
    else:
        cursor.execute("INSERT INTO sessions (id, data, updated) VALUES (%s, %s, %s)", (sid, sdata, sdt))
    if user_id != None:
        cursor.execute("UPDATE sessions SET owner_id = %s WHERE id = %s", (user_id, sid))
        
    conn.commit()

@enmodal_map.route('/session_save')
def route_session_save():
    h = int(request.args.get('i'), 16)
    e = check_for_session_errors(h)
    if e:
        return e

    a = session_manager.auth_by_key(h)

    if a.editable:
        save_session(a.session, 0, True)
        del a
        return json.dumps({"result": "OK"})
    else:
        del a
        return json.dumps({"result": "FAIL", "message": "Non-editable session"})

@enmodal_map.route('/session_load')
def route_session_load():
    h = int(request.args.get('i'), 16)

    conn = psycopg2.connect(SESSIONS_CONN_STRING)
    cursor = conn.cursor()

    is_private = False
    sid = session_manager.get_sid_from_public_key(h)
    #print "public guess: "+str(sid)
    cursor.execute("SELECT data, title FROM sessions WHERE id = '%s' LIMIT 1", [sid])
    if (cursor.rowcount == 0):
        sid = session_manager.get_sid_from_private_key(h)
        #print "private guess: "+str(sid)
        cursor.execute("SELECT data, title FROM sessions WHERE id = '%s' LIMIT 1", [sid])
        is_private = True
    if (cursor.rowcount == 0):
        return json.dumps({"error": "Invalid ID"})

    print sid
    row = cursor.fetchone()
    sdata = row[0]
    title = row[1]
    m = Transit.Map(0)
    m.from_json(sdata)
    m.sidf_state = 0
    m.regenerate_all_ids()
    
    if not is_private:
        s = Session()
        session_manager.add(s)
        s.map = m
    else:
        s = session_manager.get_by_sid(sid)
        if s is None:
            s = Session()
            s.sid = sid
            s.map = m
            session_manager.add(s)
        else:
            s.map = m
    
    a = session_manager.auth_by_key(s.private_key())
    return_obj = {"public_key": '{:16x}'.format(a.session.public_key()), "is_private": a.editable, "title": title, "data": m.to_json()}
    if a.editable:
        return_obj["private_key"] = '{:16x}'.format(a.session.private_key())
    del a
    return json.dumps(return_obj)

@enmodal_map.route('/session_push', methods=['GET', 'POST'])
def route_session_push():
    h = int(request.args.get('i'), 16)
    e = check_for_session_errors(h)
    if e:
        return e

    print 'received session push'
    data = request.get_data()
    charset = request.mimetype_params.get('charset') or 'UTF-8'
    jd = LZString().decompressFromUTF16(data.decode(charset, 'utf-16'))
    print 'decompressed session push'
    jdl = json.loads(jd)
    d = jdl['map']
    #print d
    d['sidf_state'] = 0
    m = Transit.Map(0)
    m.from_json(d)
    m.sidf_state = 0
    
    # Copy old map
    om = session_manager.auth_by_key(h).session.map
    
    # Save new map
    session_manager.auth_by_key(h).session.map = m
    
    # Save gids
    for service in om.services:
        for station in service.stations:
            if station.hexagons_known:
                for service_n in m.services:
                    for station_n in service_n.stations:
                        if station.location == station_n.location:
                            station_n.set_hexagons(station.hexagons)
    
    # Copy user settings
    # TODO clean this up!
    settings = jdl['settings']
    m.settings.from_json(settings)

    return json.dumps({"result": "OK"})