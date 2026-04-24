"""
Land Record & File Tracking System
Single-file Flask application - deployable on Render, Railway, Heroku, etc.
"""
import os, json, sqlite3, hashlib, secrets
from datetime import datetime, date
from functools import wraps
from flask import Flask, request, session, jsonify, redirect, url_for, render_template_string

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', secrets.token_hex(32))
DB = os.environ.get('DATABASE', 'land_records.db')

# ─────────────────────────────────────────────────────────────────────────────
# DATABASE
# ─────────────────────────────────────────────────────────────────────────────
def get_db():
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn

def init_db():
    with get_db() as db:
        db.executescript("""
        CREATE TABLE IF NOT EXISTS states (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE NOT NULL
        );
        CREATE TABLE IF NOT EXISTS divisions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            state_id INTEGER NOT NULL REFERENCES states(id) ON DELETE CASCADE,
            UNIQUE(name, state_id)
        );
        CREATE TABLE IF NOT EXISTS districts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            division_id INTEGER NOT NULL REFERENCES divisions(id) ON DELETE CASCADE,
            UNIQUE(name, division_id)
        );
        CREATE TABLE IF NOT EXISTS tehsils (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            district_id INTEGER NOT NULL REFERENCES districts(id) ON DELETE CASCADE,
            UNIQUE(name, district_id)
        );
        CREATE TABLE IF NOT EXISTS mauzas (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            tehsil_id INTEGER NOT NULL REFERENCES tehsils(id) ON DELETE CASCADE,
            UNIQUE(name, tehsil_id)
        );
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL,
            role TEXT NOT NULL CHECK(role IN ('field_office','arc','ac','dc','commissioner','dg','admin')),
            state_id INTEGER REFERENCES states(id),
            division_id INTEGER REFERENCES divisions(id),
            district_id INTEGER REFERENCES districts(id),
            tehsil_id INTEGER REFERENCES tehsils(id),
            created_at TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS files (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            mauza_id INTEGER NOT NULL REFERENCES mauzas(id),
            khewat_no TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'Submitted'
                CHECK(status IN ('Submitted','Verified','Returned','Mutation Entered','Completed')),
            submitted_by INTEGER NOT NULL REFERENCES users(id),
            submitted_date TEXT DEFAULT (date('now')),
            remarks TEXT,
            mutation_no TEXT,
            tehsil_id INTEGER NOT NULL REFERENCES tehsils(id),
            district_id INTEGER NOT NULL REFERENCES districts(id),
            division_id INTEGER NOT NULL REFERENCES divisions(id),
            state_id INTEGER NOT NULL REFERENCES states(id),
            updated_at TEXT DEFAULT (datetime('now')),
            updated_by INTEGER REFERENCES users(id)
        );
        """)
        # Seed admin if not exists
        pw = hashlib.sha256('admin123'.encode()).hexdigest()
        db.execute("""INSERT OR IGNORE INTO users(username,password,role)
                      VALUES('admin',?,'admin')""",(pw,))
        # Seed sample hierarchy
        db.execute("INSERT OR IGNORE INTO states(name) VALUES('Punjab')")
        state = db.execute("SELECT id FROM states WHERE name='Punjab'").fetchone()
        if state:
            db.execute("INSERT OR IGNORE INTO divisions(name,state_id) VALUES('Lahore Division',?)",(state['id'],))
            div = db.execute("SELECT id FROM divisions WHERE name='Lahore Division'").fetchone()
            if div:
                db.execute("INSERT OR IGNORE INTO districts(name,division_id) VALUES('Lahore',?)",(div['id'],))
                dist = db.execute("SELECT id FROM districts WHERE name='Lahore'").fetchone()
                if dist:
                    db.execute("INSERT OR IGNORE INTO tehsils(name,district_id) VALUES('Shalimar',?)",(dist['id'],))
                    teh = db.execute("SELECT id FROM tehsils WHERE name='Shalimar'").fetchone()
                    if teh:
                        db.execute("INSERT OR IGNORE INTO mauzas(name,tehsil_id) VALUES('Mauza Baghbanpura',?)",(teh['id'],))
                        db.execute("INSERT OR IGNORE INTO mauzas(name,tehsil_id) VALUES('Mauza Shahdara',?)",(teh['id'],))
                        # Seed test users
                        roles_data = [
                            ('field1','field123','field_office', state['id'], div['id'], dist['id'], teh['id']),
                            ('arc1','arc123','arc', state['id'], div['id'], dist['id'], teh['id']),
                            ('ac1','ac123','ac', state['id'], div['id'], dist['id'], None),
                            ('dc1','dc123','dc', state['id'], div['id'], dist['id'], None),
                            ('comm1','comm123','commissioner', state['id'], div['id'], None, None),
                            ('dg1','dg123','dg', None, None, None, None),
                        ]
                        for r in roles_data:
                            ph = hashlib.sha256(r[1].encode()).hexdigest()
                            db.execute("""INSERT OR IGNORE INTO users
                                (username,password,role,state_id,division_id,district_id,tehsil_id)
                                VALUES(?,?,?,?,?,?,?)""",
                                (r[0],ph,r[2],r[3],r[4],r[5],r[6]))
        db.commit()

# ─────────────────────────────────────────────────────────────────────────────
# AUTH HELPERS
# ─────────────────────────────────────────────────────────────────────────────
def hash_pw(pw): return hashlib.sha256(pw.encode()).hexdigest()

def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'user_id' not in session:
            return jsonify({'error':'Unauthorized'}), 401
        return f(*args, **kwargs)
    return decorated

def role_required(*roles):
    def decorator(f):
        @wraps(f)
        def decorated(*args, **kwargs):
            if session.get('role') not in roles:
                return jsonify({'error':'Forbidden'}), 403
            return f(*args, **kwargs)
        return decorated
    return decorator

def current_user():
    if 'user_id' not in session:
        return None
    with get_db() as db:
        return db.execute("SELECT * FROM users WHERE id=?", (session['user_id'],)).fetchone()

def geo_filter(query_params):
    """Build WHERE clause based on user's geographic scope"""
    u = current_user()
    if not u: return {}, []
    role = u['role']
    filters = {}
    if role in ('field_office','arc'):
        if u['tehsil_id']: filters['tehsil_id'] = u['tehsil_id']
    elif role == 'ac':
        if u['district_id']: filters['district_id'] = u['district_id']
    elif role == 'dc':
        if u['district_id']: filters['district_id'] = u['district_id']
    elif role == 'commissioner':
        if u['division_id']: filters['division_id'] = u['division_id']
    elif role == 'dg':
        if u['state_id']: filters['state_id'] = u['state_id']
    # admin & dg with no state see everything
    # apply extra query params filters
    for key in ('state_id','division_id','district_id','tehsil_id','status','mauza_id'):
        if query_params.get(key):
            # don't override mandatory geo restrictions
            if key not in filters:
                filters[key] = query_params[key]
    return filters

# ─────────────────────────────────────────────────────────────────────────────
# API ROUTES
# ─────────────────────────────────────────────────────────────────────────────
@app.route('/api/login', methods=['POST'])
def api_login():
    data = request.json
    with get_db() as db:
        u = db.execute("SELECT * FROM users WHERE username=?", (data.get('username',''),)).fetchone()
    if u and u['password'] == hash_pw(data.get('password','')):
        session.permanent = True
        session['user_id'] = u['id']
        session['role'] = u['role']
        session['username'] = u['username']
        return jsonify({'role': u['role'], 'username': u['username']})
    return jsonify({'error': 'Invalid credentials'}), 401

@app.route('/api/logout', methods=['POST'])
def api_logout():
    session.clear()
    return jsonify({'ok': True})

@app.route('/api/me')
def api_me():
    if 'user_id' not in session:
        return jsonify(None)
    u = current_user()
    if not u: return jsonify(None)
    return jsonify({
        'id': u['id'], 'username': u['username'], 'role': u['role'],
        'state_id': u['state_id'], 'division_id': u['division_id'],
        'district_id': u['district_id'], 'tehsil_id': u['tehsil_id']
    })

# ── Geography ────────────────────────────────────────────────────────────────
@app.route('/api/states')
@login_required
def api_states():
    with get_db() as db:
        rows = db.execute("SELECT * FROM states ORDER BY name").fetchall()
    return jsonify([dict(r) for r in rows])

@app.route('/api/states', methods=['POST'])
@login_required
@role_required('admin')
def api_create_state():
    data = request.json
    names = data.get('names') if isinstance(data.get('names'), list) else [data.get('name')]
    created = []
    with get_db() as db:
        for name in names:
            if name:
                try:
                    cur = db.execute("INSERT INTO states(name) VALUES(?)", (name.strip(),))
                    created.append({'id': cur.lastrowid, 'name': name.strip()})
                except: pass
        db.commit()
    return jsonify(created), 201

@app.route('/api/states/<int:sid>', methods=['PUT'])
@login_required
@role_required('admin')
def api_update_state(sid):
    data = request.json
    with get_db() as db:
        db.execute("UPDATE states SET name=? WHERE id=?", (data['name'], sid))
        db.commit()
    return jsonify({'ok': True})

@app.route('/api/states/<int:sid>', methods=['DELETE'])
@login_required
@role_required('admin')
def api_delete_state(sid):
    with get_db() as db:
        db.execute("DELETE FROM states WHERE id=?", (sid,))
        db.commit()
    return jsonify({'ok': True})

@app.route('/api/divisions')
@login_required
def api_divisions():
    sid = request.args.get('state_id')
    with get_db() as db:
        if sid:
            rows = db.execute("SELECT d.*,s.name as state_name FROM divisions d JOIN states s ON s.id=d.state_id WHERE d.state_id=? ORDER BY d.name",(sid,)).fetchall()
        else:
            rows = db.execute("SELECT d.*,s.name as state_name FROM divisions d JOIN states s ON s.id=d.state_id ORDER BY d.name").fetchall()
    return jsonify([dict(r) for r in rows])

@app.route('/api/divisions', methods=['POST'])
@login_required
@role_required('admin')
def api_create_division():
    data = request.json
    names = data.get('names') if isinstance(data.get('names'), list) else [data.get('name')]
    state_id = data.get('state_id')
    created = []
    with get_db() as db:
        for name in names:
            if name:
                try:
                    cur = db.execute("INSERT INTO divisions(name,state_id) VALUES(?,?)", (name.strip(), state_id))
                    created.append({'id': cur.lastrowid, 'name': name.strip(), 'state_id': state_id})
                except: pass
        db.commit()
    return jsonify(created), 201

@app.route('/api/divisions/<int:did>', methods=['PUT'])
@login_required
@role_required('admin')
def api_update_division(did):
    data = request.json
    with get_db() as db:
        db.execute("UPDATE divisions SET name=?,state_id=? WHERE id=?", (data['name'], data['state_id'], did))
        db.commit()
    return jsonify({'ok': True})

@app.route('/api/divisions/<int:did>', methods=['DELETE'])
@login_required
@role_required('admin')
def api_delete_division(did):
    with get_db() as db:
        db.execute("DELETE FROM divisions WHERE id=?", (did,))
        db.commit()
    return jsonify({'ok': True})

@app.route('/api/districts')
@login_required
def api_districts():
    did = request.args.get('division_id')
    with get_db() as db:
        if did:
            rows = db.execute("SELECT dt.*,dv.name as division_name,s.name as state_name FROM districts dt JOIN divisions dv ON dv.id=dt.division_id JOIN states s ON s.id=dv.state_id WHERE dt.division_id=? ORDER BY dt.name",(did,)).fetchall()
        else:
            rows = db.execute("SELECT dt.*,dv.name as division_name,s.name as state_name FROM districts dt JOIN divisions dv ON dv.id=dt.division_id JOIN states s ON s.id=dv.state_id ORDER BY dt.name").fetchall()
    return jsonify([dict(r) for r in rows])

@app.route('/api/districts', methods=['POST'])
@login_required
@role_required('admin')
def api_create_district():
    data = request.json
    names = data.get('names') if isinstance(data.get('names'), list) else [data.get('name')]
    division_id = data.get('division_id')
    created = []
    with get_db() as db:
        for name in names:
            if name:
                try:
                    cur = db.execute("INSERT INTO districts(name,division_id) VALUES(?,?)", (name.strip(), division_id))
                    created.append({'id': cur.lastrowid, 'name': name.strip()})
                except: pass
        db.commit()
    return jsonify(created), 201

@app.route('/api/districts/<int:did>', methods=['PUT'])
@login_required
@role_required('admin')
def api_update_district(did):
    data = request.json
    with get_db() as db:
        db.execute("UPDATE districts SET name=?,division_id=? WHERE id=?", (data['name'], data['division_id'], did))
        db.commit()
    return jsonify({'ok': True})

@app.route('/api/districts/<int:did>', methods=['DELETE'])
@login_required
@role_required('admin')
def api_delete_district(did):
    with get_db() as db:
        db.execute("DELETE FROM districts WHERE id=?", (did,))
        db.commit()
    return jsonify({'ok': True})

@app.route('/api/tehsils')
@login_required
def api_tehsils():
    did = request.args.get('district_id')
    with get_db() as db:
        if did:
            rows = db.execute("SELECT t.*,dt.name as district_name FROM tehsils t JOIN districts dt ON dt.id=t.district_id WHERE t.district_id=? ORDER BY t.name",(did,)).fetchall()
        else:
            rows = db.execute("SELECT t.*,dt.name as district_name FROM tehsils t JOIN districts dt ON dt.id=t.district_id ORDER BY t.name").fetchall()
    return jsonify([dict(r) for r in rows])

@app.route('/api/tehsils', methods=['POST'])
@login_required
@role_required('admin')
def api_create_tehsil():
    data = request.json
    names = data.get('names') if isinstance(data.get('names'), list) else [data.get('name')]
    district_id = data.get('district_id')
    created = []
    with get_db() as db:
        for name in names:
            if name:
                try:
                    cur = db.execute("INSERT INTO tehsils(name,district_id) VALUES(?,?)", (name.strip(), district_id))
                    created.append({'id': cur.lastrowid, 'name': name.strip()})
                except: pass
        db.commit()
    return jsonify(created), 201

@app.route('/api/tehsils/<int:tid>', methods=['PUT'])
@login_required
@role_required('admin')
def api_update_tehsil(tid):
    data = request.json
    with get_db() as db:
        db.execute("UPDATE tehsils SET name=?,district_id=? WHERE id=?", (data['name'], data['district_id'], tid))
        db.commit()
    return jsonify({'ok': True})

@app.route('/api/tehsils/<int:tid>', methods=['DELETE'])
@login_required
@role_required('admin')
def api_delete_tehsil(tid):
    with get_db() as db:
        db.execute("DELETE FROM tehsils WHERE id=?", (tid,))
        db.commit()
    return jsonify({'ok': True})

@app.route('/api/mauzas')
@login_required
def api_mauzas():
    tid = request.args.get('tehsil_id')
    with get_db() as db:
        if tid:
            rows = db.execute("SELECT m.*,t.name as tehsil_name FROM mauzas m JOIN tehsils t ON t.id=m.tehsil_id WHERE m.tehsil_id=? ORDER BY m.name",(tid,)).fetchall()
        else:
            rows = db.execute("SELECT m.*,t.name as tehsil_name FROM mauzas m JOIN tehsils t ON t.id=m.tehsil_id ORDER BY m.name").fetchall()
    return jsonify([dict(r) for r in rows])

@app.route('/api/mauzas', methods=['POST'])
@login_required
@role_required('admin')
def api_create_mauza():
    data = request.json
    names = data.get('names') if isinstance(data.get('names'), list) else [data.get('name')]
    tehsil_id = data.get('tehsil_id')
    created = []
    with get_db() as db:
        for name in names:
            if name:
                try:
                    cur = db.execute("INSERT INTO mauzas(name,tehsil_id) VALUES(?,?)", (name.strip(), tehsil_id))
                    created.append({'id': cur.lastrowid, 'name': name.strip()})
                except: pass
        db.commit()
    return jsonify(created), 201

@app.route('/api/mauzas/<int:mid>', methods=['PUT'])
@login_required
@role_required('admin')
def api_update_mauza(mid):
    data = request.json
    with get_db() as db:
        db.execute("UPDATE mauzas SET name=?,tehsil_id=? WHERE id=?", (data['name'], data['tehsil_id'], mid))
        db.commit()
    return jsonify({'ok': True})

@app.route('/api/mauzas/<int:mid>', methods=['DELETE'])
@login_required
@role_required('admin')
def api_delete_mauza(mid):
    with get_db() as db:
        db.execute("DELETE FROM mauzas WHERE id=?", (mid,))
        db.commit()
    return jsonify({'ok': True})

# ── Users ────────────────────────────────────────────────────────────────────
@app.route('/api/users')
@login_required
@role_required('admin')
def api_users():
    with get_db() as db:
        rows = db.execute("""SELECT u.id,u.username,u.role,u.created_at,
            s.name as state_name, dv.name as division_name,
            dt.name as district_name, t.name as tehsil_name
            FROM users u
            LEFT JOIN states s ON s.id=u.state_id
            LEFT JOIN divisions dv ON dv.id=u.division_id
            LEFT JOIN districts dt ON dt.id=u.district_id
            LEFT JOIN tehsils t ON t.id=u.tehsil_id
            ORDER BY u.username""").fetchall()
    return jsonify([dict(r) for r in rows])

@app.route('/api/users', methods=['POST'])
@login_required
@role_required('admin')
def api_create_user():
    data = request.json
    users_list = data.get('users') if isinstance(data.get('users'), list) else [data]
    created = []
    with get_db() as db:
        for u in users_list:
            try:
                pw = hash_pw(u.get('password','changeme'))
                cur = db.execute("""INSERT INTO users(username,password,role,state_id,division_id,district_id,tehsil_id)
                    VALUES(?,?,?,?,?,?,?)""",
                    (u['username'],pw,u['role'],
                     u.get('state_id'),u.get('division_id'),u.get('district_id'),u.get('tehsil_id')))
                created.append({'id': cur.lastrowid, 'username': u['username']})
            except Exception as e:
                created.append({'error': str(e), 'username': u.get('username')})
        db.commit()
    return jsonify(created), 201

@app.route('/api/users/<int:uid>', methods=['PUT'])
@login_required
@role_required('admin')
def api_update_user(uid):
    data = request.json
    with get_db() as db:
        if data.get('password'):
            db.execute("""UPDATE users SET username=?,password=?,role=?,state_id=?,division_id=?,district_id=?,tehsil_id=?
                WHERE id=?""", (data['username'],hash_pw(data['password']),data['role'],
                data.get('state_id'),data.get('division_id'),data.get('district_id'),data.get('tehsil_id'),uid))
        else:
            db.execute("""UPDATE users SET username=?,role=?,state_id=?,division_id=?,district_id=?,tehsil_id=?
                WHERE id=?""", (data['username'],data['role'],
                data.get('state_id'),data.get('division_id'),data.get('district_id'),data.get('tehsil_id'),uid))
        db.commit()
    return jsonify({'ok': True})

@app.route('/api/users/<int:uid>', methods=['DELETE'])
@login_required
@role_required('admin')
def api_delete_user(uid):
    if uid == session['user_id']:
        return jsonify({'error': 'Cannot delete yourself'}), 400
    with get_db() as db:
        db.execute("DELETE FROM users WHERE id=?", (uid,))
        db.commit()
    return jsonify({'ok': True})

# ── Files ────────────────────────────────────────────────────────────────────
@app.route('/api/files')
@login_required
def api_files():
    role = session.get('role')
    u = current_user()
    qp = request.args.to_dict()
    with get_db() as db:
        base = """SELECT f.*,
            m.name as mauza_name, t.name as tehsil_name,
            dt.name as district_name, dv.name as division_name,
            s.name as state_name, su.username as submitted_by_name,
            uu.username as updated_by_name
            FROM files f
            JOIN mauzas m ON m.id=f.mauza_id
            JOIN tehsils t ON t.id=f.tehsil_id
            JOIN districts dt ON dt.id=f.district_id
            JOIN divisions dv ON dv.id=f.division_id
            JOIN states s ON s.id=f.state_id
            JOIN users su ON su.id=f.submitted_by
            LEFT JOIN users uu ON uu.id=f.updated_by
            WHERE 1=1"""
        params = []
        # Field office: only their tehsil files + Returned status visible
        if role == 'field_office':
            base += " AND f.tehsil_id=?"
            params.append(u['tehsil_id'])
            base += " AND (f.submitted_by=? OR f.status='Returned')"
            params.append(u['id'])
        elif role == 'arc':
            base += " AND f.tehsil_id=?"
            params.append(u['tehsil_id'])
        elif role == 'ac':
            base += " AND f.district_id=?"
            params.append(u['district_id'])
        elif role == 'dc':
            base += " AND f.district_id=?"
            params.append(u['district_id'])
        elif role == 'commissioner':
            base += " AND f.division_id=?"
            params.append(u['division_id'])
        elif role == 'dg':
            if u['state_id']:
                base += " AND f.state_id=?"
                params.append(u['state_id'])
        # admin sees all

        # Extra filters from query params
        for col in ('status','tehsil_id','district_id','division_id','state_id','mauza_id'):
            if qp.get(col):
                base += f" AND f.{col}=?"
                params.append(qp[col])
        if qp.get('search'):
            base += " AND (f.khewat_no LIKE ? OR m.name LIKE ? OR f.mutation_no LIKE ?)"
            s = f"%{qp['search']}%"
            params += [s,s,s]

        base += " ORDER BY f.id DESC LIMIT 500"
        rows = db.execute(base, params).fetchall()
    return jsonify([dict(r) for r in rows])

@app.route('/api/files', methods=['POST'])
@login_required
@role_required('field_office')
def api_create_file():
    u = current_user()
    data = request.json
    files_list = data.get('files') if isinstance(data.get('files'), list) else [data]
    created = []
    with get_db() as db:
        for f in files_list:
            mauza_id = f.get('mauza_id')
            # Verify mauza belongs to user's tehsil
            mauza = db.execute("SELECT * FROM mauzas WHERE id=?", (mauza_id,)).fetchone()
            if not mauza or mauza['tehsil_id'] != u['tehsil_id']:
                created.append({'error': 'Invalid mauza', 'khewat_no': f.get('khewat_no')})
                continue
            tehsil = db.execute("SELECT t.*,dt.division_id,dv.state_id FROM tehsils t JOIN districts dt ON dt.id=t.district_id JOIN divisions dv ON dv.id=dt.division_id WHERE t.id=?",(u['tehsil_id'],)).fetchone()
            cur = db.execute("""INSERT INTO files
                (mauza_id,khewat_no,status,submitted_by,submitted_date,remarks,
                tehsil_id,district_id,division_id,state_id)
                VALUES(?,?,?,?,?,?,?,?,?,?)""",
                (mauza_id, f['khewat_no'], 'Submitted', u['id'],
                 f.get('submitted_date', date.today().isoformat()),
                 f.get('remarks'), u['tehsil_id'], tehsil['district_id'],
                 tehsil['division_id'], tehsil['state_id']))
            created.append({'id': cur.lastrowid, 'khewat_no': f['khewat_no']})
        db.commit()
    return jsonify(created), 201

@app.route('/api/files/<int:fid>', methods=['PUT'])
@login_required
def api_update_file(fid):
    role = session.get('role')
    u = current_user()
    data = request.json
    with get_db() as db:
        f = db.execute("SELECT * FROM files WHERE id=?", (fid,)).fetchone()
        if not f:
            return jsonify({'error': 'Not found'}), 404

        new_status = data.get('status', f['status'])
        mutation_no = data.get('mutation_no', f['mutation_no'])
        remarks = data.get('remarks', f['remarks'])

        # Workflow transitions enforcement
        allowed = False
        if role == 'arc':
            # ARC can Verify, Return, enter Mutation No, pass Mutation
            if f['tehsil_id'] == u['tehsil_id']:
                if new_status in ('Verified','Returned','Mutation Entered','Completed'):
                    allowed = True
        elif role in ('ac','dc','commissioner','dg','admin'):
            allowed = True
        elif role == 'field_office':
            # Field office can only add remarks on returned files
            if f['submitted_by'] == u['id'] and f['status'] == 'Returned':
                allowed = True
                new_status = f['status']  # can't change status

        if not allowed:
            return jsonify({'error': 'Action not permitted'}), 403

        db.execute("""UPDATE files SET status=?,mutation_no=?,remarks=?,updated_at=?,updated_by=?
            WHERE id=?""",
            (new_status, mutation_no, remarks, datetime.now().isoformat(), u['id'], fid))
        db.commit()
    return jsonify({'ok': True})

@app.route('/api/files/<int:fid>', methods=['DELETE'])
@login_required
@role_required('admin')
def api_delete_file(fid):
    with get_db() as db:
        db.execute("DELETE FROM files WHERE id=?", (fid,))
        db.commit()
    return jsonify({'ok': True})

@app.route('/api/stats')
@login_required
def api_stats():
    role = session.get('role')
    u = current_user()
    with get_db() as db:
        base = "SELECT status, COUNT(*) as cnt FROM files WHERE 1=1"
        params = []
        if role == 'field_office':
            base += " AND tehsil_id=? AND submitted_by=?"
            params += [u['tehsil_id'], u['id']]
        elif role == 'arc':
            base += " AND tehsil_id=?"
            params.append(u['tehsil_id'])
        elif role in ('ac','dc'):
            base += " AND district_id=?"
            params.append(u['district_id'])
        elif role == 'commissioner':
            base += " AND division_id=?"
            params.append(u['division_id'])
        elif role == 'dg':
            if u['state_id']:
                base += " AND state_id=?"
                params.append(u['state_id'])
        base += " GROUP BY status"
        rows = db.execute(base, params).fetchall()
        stats = {r['status']: r['cnt'] for r in rows}

        # Monthly trend (last 6 months)
        trend_rows = db.execute("""SELECT strftime('%Y-%m',submitted_date) as month, COUNT(*) as cnt
            FROM files WHERE 1=1 """ + (" AND tehsil_id=? AND submitted_by=?" if role=='field_office' else
            " AND tehsil_id=?" if role=='arc' else
            " AND district_id=?" if role in ('ac','dc') else
            " AND division_id=?" if role=='commissioner' else
            " AND state_id=?" if role=='dg' and u['state_id'] else "") +
            " GROUP BY month ORDER BY month DESC LIMIT 6",
            params[:2] if role=='field_office' else params).fetchall()
        trend = [dict(r) for r in trend_rows]
    return jsonify({'stats': stats, 'trend': trend})

# ─────────────────────────────────────────────────────────────────────────────
# FRONTEND - Single Page Application
# ─────────────────────────────────────────────────────────────────────────────
HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>Land Record & File Tracking System</title>
<script src="https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.1/chart.umd.min.js"></script>
<link href="https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500&family=IBM+Plex+Sans:wght@300;400;500;600&display=swap" rel="stylesheet">
<style>
:root {
  --bg: #0d1117;
  --surface: #161b22;
  --surface2: #21262d;
  --border: #30363d;
  --text: #e6edf3;
  --text-muted: #7d8590;
  --accent: #1f6feb;
  --accent-hover: #388bfd;
  --green: #3fb950;
  --yellow: #d29922;
  --red: #f85149;
  --orange: #db6d28;
  --purple: #8b949e;
  --sidebar-w: 240px;
}
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:'IBM Plex Sans',sans-serif;background:var(--bg);color:var(--text);min-height:100vh;display:flex;flex-direction:column}
a{color:var(--accent);text-decoration:none}
button{cursor:pointer;font-family:inherit}
input,select,textarea{font-family:inherit}

/* LOGIN */
#login-page{display:flex;align-items:center;justify-content:center;min-height:100vh;background:var(--bg)}
.login-box{background:var(--surface);border:1px solid var(--border);border-radius:12px;padding:48px 40px;width:380px;box-shadow:0 24px 64px rgba(0,0,0,.5)}
.login-logo{text-align:center;margin-bottom:32px}
.login-logo .logo-icon{width:56px;height:56px;background:var(--accent);border-radius:12px;display:inline-flex;align-items:center;justify-content:center;font-size:24px;margin-bottom:12px}
.login-logo h1{font-size:18px;font-weight:600;color:var(--text)}
.login-logo p{font-size:12px;color:var(--text-muted);margin-top:4px;font-family:'IBM Plex Mono',monospace}
.form-group{margin-bottom:16px}
.form-group label{display:block;font-size:12px;font-weight:500;color:var(--text-muted);margin-bottom:6px;letter-spacing:.05em;text-transform:uppercase}
.form-group input{width:100%;background:var(--bg);border:1px solid var(--border);border-radius:6px;padding:10px 12px;color:var(--text);font-size:14px;transition:border-color .15s}
.form-group input:focus{outline:none;border-color:var(--accent)}
.btn-primary{width:100%;background:var(--accent);color:#fff;border:none;border-radius:6px;padding:11px;font-size:14px;font-weight:500;transition:background .15s}
.btn-primary:hover{background:var(--accent-hover)}
.login-error{color:var(--red);font-size:13px;text-align:center;margin-top:12px;min-height:20px}
.login-hint{margin-top:20px;padding:12px;background:var(--bg);border-radius:6px;border:1px solid var(--border);font-size:11px;color:var(--text-muted);font-family:'IBM Plex Mono',monospace}
.login-hint p{margin-bottom:3px}

/* APP LAYOUT */
#app{display:none;min-height:100vh;flex-direction:row}
#sidebar{width:var(--sidebar-w);background:var(--surface);border-right:1px solid var(--border);display:flex;flex-direction:column;position:fixed;top:0;left:0;bottom:0;z-index:100}
.sidebar-header{padding:20px 16px;border-bottom:1px solid var(--border)}
.sidebar-header .app-name{font-size:13px;font-weight:600;color:var(--text);letter-spacing:.02em}
.sidebar-header .app-sub{font-size:10px;color:var(--text-muted);font-family:'IBM Plex Mono',monospace;margin-top:2px}
.sidebar-user{padding:12px 16px;border-bottom:1px solid var(--border);display:flex;align-items:center;gap:10px}
.avatar{width:32px;height:32px;border-radius:8px;background:var(--accent);display:flex;align-items:center;justify-content:center;font-size:13px;font-weight:600;flex-shrink:0}
.sidebar-user-info{flex:1;min-width:0}
.sidebar-username{font-size:13px;font-weight:500;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.sidebar-role{font-size:10px;color:var(--text-muted);font-family:'IBM Plex Mono',monospace;text-transform:uppercase}
nav{flex:1;padding:12px 8px;overflow-y:auto}
.nav-section{margin-bottom:4px}
.nav-label{font-size:10px;color:var(--text-muted);text-transform:uppercase;letter-spacing:.08em;padding:8px 8px 4px;font-weight:600}
.nav-item{display:flex;align-items:center;gap:10px;padding:8px 10px;border-radius:6px;font-size:13px;color:var(--text-muted);cursor:pointer;transition:all .15s;margin-bottom:1px;border:none;background:none;width:100%;text-align:left}
.nav-item:hover{background:var(--surface2);color:var(--text)}
.nav-item.active{background:var(--accent);color:#fff}
.nav-item .nav-icon{width:16px;text-align:center;flex-shrink:0}
.sidebar-footer{padding:12px 8px;border-top:1px solid var(--border)}

#main{margin-left:var(--sidebar-w);flex:1;display:flex;flex-direction:column;min-height:100vh}
.topbar{padding:16px 24px;border-bottom:1px solid var(--border);display:flex;align-items:center;justify-content:space-between;background:var(--surface);position:sticky;top:0;z-index:50}
.topbar-title{font-size:15px;font-weight:600}
.topbar-actions{display:flex;gap:8px;align-items:center}
.content{padding:24px;flex:1}

/* BUTTONS */
.btn{display:inline-flex;align-items:center;gap:6px;padding:7px 14px;border-radius:6px;font-size:13px;font-weight:500;border:1px solid var(--border);background:var(--surface2);color:var(--text);transition:all .15s;cursor:pointer}
.btn:hover{border-color:var(--accent);color:var(--accent)}
.btn-sm{padding:4px 10px;font-size:12px}
.btn-accent{background:var(--accent);border-color:var(--accent);color:#fff}
.btn-accent:hover{background:var(--accent-hover);border-color:var(--accent-hover);color:#fff}
.btn-danger{background:rgba(248,81,73,.1);border-color:var(--red);color:var(--red)}
.btn-danger:hover{background:var(--red);color:#fff}
.btn-success{background:rgba(63,185,80,.1);border-color:var(--green);color:var(--green)}
.btn-success:hover{background:var(--green);color:#fff}
.btn-warn{background:rgba(210,153,34,.1);border-color:var(--yellow);color:var(--yellow)}
.btn-warn:hover{background:var(--yellow);color:#fff}

/* CARDS */
.card{background:var(--surface);border:1px solid var(--border);border-radius:8px;padding:20px}
.stats-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(160px,1fr));gap:16px;margin-bottom:24px}
.stat-card{background:var(--surface);border:1px solid var(--border);border-radius:8px;padding:20px}
.stat-label{font-size:11px;color:var(--text-muted);text-transform:uppercase;letter-spacing:.06em;margin-bottom:8px;font-weight:600}
.stat-value{font-size:32px;font-weight:600;font-family:'IBM Plex Mono',monospace}
.stat-value.green{color:var(--green)}
.stat-value.yellow{color:var(--yellow)}
.stat-value.red{color:var(--red)}
.stat-value.blue{color:var(--accent-hover)}
.stat-value.orange{color:var(--orange)}

/* TABLE */
.table-wrap{overflow-x:auto;border-radius:8px;border:1px solid var(--border)}
table{width:100%;border-collapse:collapse;font-size:13px}
thead{background:var(--surface2)}
th{padding:10px 14px;text-align:left;font-size:11px;font-weight:600;text-transform:uppercase;letter-spacing:.06em;color:var(--text-muted);border-bottom:1px solid var(--border);white-space:nowrap}
td{padding:10px 14px;border-bottom:1px solid var(--border);color:var(--text);vertical-align:middle}
tr:last-child td{border-bottom:none}
tr:hover td{background:rgba(255,255,255,.02)}
.mono{font-family:'IBM Plex Mono',monospace;font-size:12px}

/* BADGES */
.badge{display:inline-flex;align-items:center;padding:2px 8px;border-radius:20px;font-size:11px;font-weight:600;font-family:'IBM Plex Mono',monospace;white-space:nowrap}
.badge-submitted{background:rgba(100,130,200,.15);color:#88aaee;border:1px solid rgba(100,130,200,.3)}
.badge-verified{background:rgba(63,185,80,.15);color:var(--green);border:1px solid rgba(63,185,80,.3)}
.badge-returned{background:rgba(248,81,73,.15);color:var(--red);border:1px solid rgba(248,81,73,.3)}
.badge-mutation{background:rgba(210,153,34,.15);color:var(--yellow);border:1px solid rgba(210,153,34,.3)}
.badge-completed{background:rgba(63,185,80,.25);color:#56d364;border:1px solid rgba(63,185,80,.4)}

/* FILTERS */
.filters{display:flex;gap:10px;flex-wrap:wrap;margin-bottom:16px;align-items:center}
.filter-select,.filter-input{background:var(--surface2);border:1px solid var(--border);border-radius:6px;padding:7px 10px;color:var(--text);font-size:13px;font-family:inherit}
.filter-select:focus,.filter-input:focus{outline:none;border-color:var(--accent)}

/* MODAL */
.modal-overlay{display:none;position:fixed;inset:0;background:rgba(0,0,0,.7);z-index:200;align-items:center;justify-content:center}
.modal-overlay.open{display:flex}
.modal{background:var(--surface);border:1px solid var(--border);border-radius:12px;padding:28px;width:560px;max-width:95vw;max-height:90vh;overflow-y:auto;box-shadow:0 32px 80px rgba(0,0,0,.6)}
.modal-header{display:flex;justify-content:space-between;align-items:center;margin-bottom:24px}
.modal-title{font-size:16px;font-weight:600}
.modal-close{background:none;border:none;color:var(--text-muted);font-size:20px;cursor:pointer;line-height:1;padding:4px}
.modal-close:hover{color:var(--text)}
.form-row{display:grid;grid-template-columns:1fr 1fr;gap:14px}
.form-field{margin-bottom:14px}
.form-field label{display:block;font-size:11px;font-weight:600;color:var(--text-muted);margin-bottom:6px;text-transform:uppercase;letter-spacing:.05em}
.form-field input,.form-field select,.form-field textarea{width:100%;background:var(--bg);border:1px solid var(--border);border-radius:6px;padding:8px 10px;color:var(--text);font-size:13px;font-family:inherit}
.form-field input:focus,.form-field select:focus,.form-field textarea:focus{outline:none;border-color:var(--accent)}
.form-field textarea{resize:vertical;min-height:80px}
.modal-footer{display:flex;justify-content:flex-end;gap:10px;margin-top:20px;padding-top:16px;border-top:1px solid var(--border)}

/* CHART */
.chart-card{background:var(--surface);border:1px solid var(--border);border-radius:8px;padding:20px;margin-bottom:24px}
.chart-header{display:flex;justify-content:space-between;align-items:center;margin-bottom:20px}
.chart-title{font-size:14px;font-weight:600;color:var(--text)}
.chart-container{height:260px;position:relative}

/* TOAST */
.toast-container{position:fixed;bottom:24px;right:24px;z-index:300;display:flex;flex-direction:column;gap:8px}
.toast{background:var(--surface2);border:1px solid var(--border);border-radius:8px;padding:12px 16px;font-size:13px;box-shadow:0 8px 24px rgba(0,0,0,.4);animation:slideIn .2s ease;display:flex;align-items:center;gap:8px;min-width:260px}
.toast.success{border-left:3px solid var(--green)}
.toast.error{border-left:3px solid var(--red)}
.toast.info{border-left:3px solid var(--accent)}
@keyframes slideIn{from{transform:translateX(100%);opacity:0}to{transform:translateX(0);opacity:1}}

/* PAGINATION */
.pagination{display:flex;align-items:center;gap:4px;margin-top:16px}
.page-btn{padding:5px 10px;border:1px solid var(--border);border-radius:4px;background:var(--surface2);color:var(--text-muted);font-size:12px;cursor:pointer}
.page-btn.active{background:var(--accent);border-color:var(--accent);color:#fff}
.page-btn:hover:not(.active){border-color:var(--accent)}

/* BULK AREA */
.bulk-info{font-size:12px;color:var(--text-muted);margin-top:6px}
.tag{display:inline-block;background:var(--surface2);border:1px solid var(--border);border-radius:4px;padding:2px 6px;font-size:11px;font-family:'IBM Plex Mono',monospace;margin:2px}

.section-title{font-size:14px;font-weight:600;margin-bottom:16px;padding-bottom:8px;border-bottom:1px solid var(--border)}
.empty-state{text-align:center;padding:60px 20px;color:var(--text-muted)}
.empty-state .empty-icon{font-size:40px;margin-bottom:12px;opacity:.4}
.empty-state p{font-size:14px}

/* Role chips */
.role-chip{font-family:'IBM Plex Mono',monospace;font-size:10px;padding:2px 6px;border-radius:4px;background:var(--surface2);border:1px solid var(--border)}
.role-admin{color:#b57dff;border-color:#b57dff;background:rgba(181,125,255,.1)}
.role-dg{color:#58a6ff;border-color:#58a6ff;background:rgba(88,166,255,.1)}
.role-commissioner{color:#79c0ff;border-color:#79c0ff;background:rgba(121,192,255,.1)}
.role-dc{color:#56d364;border-color:#56d364;background:rgba(86,211,100,.1)}
.role-ac{color:#3fb950;border-color:#3fb950;background:rgba(63,185,80,.1)}
.role-arc{color:#d29922;border-color:#d29922;background:rgba(210,153,34,.1)}
.role-field_office{color:#f0883e;border-color:#f0883e;background:rgba(240,136,62,.1)}
</style>
</head>
<body>

<!-- LOGIN PAGE -->
<div id="login-page">
  <div class="login-box">
    <div class="login-logo">
      <div class="logo-icon">🗂</div>
      <h1>Land Record System</h1>
      <p>LRFTS — Secure Portal</p>
    </div>
    <div class="form-group">
      <label>Username</label>
      <input type="text" id="login-username" placeholder="Enter username" autocomplete="username">
    </div>
    <div class="form-group">
      <label>Password</label>
      <input type="password" id="login-password" placeholder="Enter password" autocomplete="current-password">
    </div>
    <button class="btn-primary" onclick="doLogin()">Sign In</button>
    <div class="login-error" id="login-error"></div>
    <div class="login-hint">
      <p>Demo: admin / admin123</p>
      <p>Field: field1 / field123</p>
      <p>ARC: arc1 / arc123 | AC: ac1 / ac123</p>
      <p>DC: dc1 / dc123 | Commissioner: comm1 / comm123</p>
      <p>DG: dg1 / dg123</p>
    </div>
  </div>
</div>

<!-- APP -->
<div id="app">
  <div id="sidebar">
    <div class="sidebar-header">
      <div class="app-name">🗂 Land Records</div>
      <div class="app-sub">File Tracking System</div>
    </div>
    <div class="sidebar-user">
      <div class="avatar" id="sidebar-avatar">A</div>
      <div class="sidebar-user-info">
        <div class="sidebar-username" id="sidebar-username">—</div>
        <div class="sidebar-role" id="sidebar-role">—</div>
      </div>
    </div>
    <nav id="nav-menu"></nav>
    <div class="sidebar-footer">
      <button class="nav-item" onclick="doLogout()" style="color:var(--red)">
        <span class="nav-icon">⎋</span> Sign Out
      </button>
    </div>
  </div>

  <div id="main">
    <div class="topbar">
      <div class="topbar-title" id="page-title">Dashboard</div>
      <div class="topbar-actions" id="topbar-actions"></div>
    </div>
    <div class="content" id="page-content"></div>
  </div>
</div>

<!-- MODAL -->
<div class="modal-overlay" id="modal-overlay">
  <div class="modal" id="modal-box">
    <div class="modal-header">
      <div class="modal-title" id="modal-title">Modal</div>
      <button class="modal-close" onclick="closeModal()">✕</button>
    </div>
    <div id="modal-body"></div>
  </div>
</div>

<!-- TOAST -->
<div class="toast-container" id="toast-container"></div>

<script>
// ─── STATE ───────────────────────────────────────────────────────────────────
let me = null;
let currentPage = 'dashboard';
let barChart = null;

// ─── API ─────────────────────────────────────────────────────────────────────
async function api(method, path, body) {
  const opts = { method, headers: {'Content-Type':'application/json'} };
  if (body) opts.body = JSON.stringify(body);
  const r = await fetch('/api' + path, opts);
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(data.error || `Error ${r.status}`);
  return data;
}

// ─── AUTH ────────────────────────────────────────────────────────────────────
async function doLogin() {
  const u = document.getElementById('login-username').value.trim();
  const p = document.getElementById('login-password').value;
  document.getElementById('login-error').textContent = '';
  try {
    me = await api('POST','/login',{username:u,password:p});
    me = await api('GET','/me');
    showApp();
  } catch(e) {
    document.getElementById('login-error').textContent = e.message;
  }
}
document.getElementById('login-password')?.addEventListener('keydown', e => { if(e.key==='Enter') doLogin(); });

async function doLogout() {
  await api('POST','/logout');
  me = null;
  document.getElementById('login-page').style.display='flex';
  document.getElementById('app').style.display='none';
  document.getElementById('login-password').value='';
}

async function initApp() {
  try {
    me = await api('GET','/me');
    if(me) showApp();
  } catch(e) {}
}

function showApp() {
  document.getElementById('login-page').style.display='none';
  document.getElementById('app').style.display='flex';
  document.getElementById('sidebar-avatar').textContent = me.username[0].toUpperCase();
  document.getElementById('sidebar-username').textContent = me.username;
  document.getElementById('sidebar-role').textContent = roleLabel(me.role);
  buildNav();
  navigateTo('dashboard');
}

// ─── NAVIGATION ──────────────────────────────────────────────────────────────
function roleLabel(r) {
  const labels = {
    field_office:'Field Office',arc:'ARC',ac:'AC',dc:'DC',
    commissioner:'Commissioner',dg:'Director General',admin:'Admin'
  };
  return labels[r] || r;
}

function getNavItems() {
  const role = me.role;
  const items = [{icon:'⊞',label:'Dashboard',page:'dashboard'}];
  if(['field_office','arc','admin'].includes(role))
    items.push({icon:'📁',label:'Files',page:'files'});
  if(['ac','dc','commissioner','dg'].includes(role))
    items.push({icon:'📊',label:'Reports & Files',page:'reports'});
  if(role==='admin') {
    items.push({icon:'─',label:'ADMINISTRATION',type:'label'});
    items.push({icon:'🌍',label:'Geography',page:'geography'});
    items.push({icon:'👥',label:'Users',page:'users'});
    items.push({icon:'📁',label:'All Files',page:'files'});
  }
  return items;
}

function buildNav() {
  const nav = document.getElementById('nav-menu');
  nav.innerHTML = getNavItems().map(item => {
    if(item.type==='label') return `<div class="nav-label">${item.label}</div>`;
    return `<button class="nav-item" id="nav-${item.page}" onclick="navigateTo('${item.page}')">
      <span class="nav-icon">${item.icon}</span>${item.label}
    </button>`;
  }).join('');
}

function navigateTo(page) {
  currentPage = page;
  document.querySelectorAll('.nav-item').forEach(el=>el.classList.remove('active'));
  const btn = document.getElementById(`nav-${page}`);
  if(btn) btn.classList.add('active');
  const pages = {
    dashboard: renderDashboard,
    files: renderFiles,
    reports: renderReports,
    geography: renderGeography,
    users: renderUsers,
  };
  (pages[page] || renderDashboard)();
}

// ─── DASHBOARD ───────────────────────────────────────────────────────────────
async function renderDashboard() {
  setPage('Dashboard','');
  const content = document.getElementById('page-content');
  content.innerHTML = '<div style="color:var(--text-muted);font-size:13px">Loading…</div>';
  try {
    const data = await api('GET','/stats');
    const stats = data.stats || {};
    const trend = (data.trend || []).reverse();
    const total = Object.values(stats).reduce((a,b)=>a+b,0);

    content.innerHTML = `
    <div class="stats-grid">
      <div class="stat-card"><div class="stat-label">Total Files</div><div class="stat-value blue">${total}</div></div>
      <div class="stat-card"><div class="stat-label">Submitted</div><div class="stat-value" style="color:#88aaee">${stats.Submitted||0}</div></div>
      <div class="stat-card"><div class="stat-label">Verified</div><div class="stat-value green">${stats.Verified||0}</div></div>
      <div class="stat-card"><div class="stat-label">Returned</div><div class="stat-value red">${stats.Returned||0}</div></div>
      <div class="stat-card"><div class="stat-label">Mutation Entered</div><div class="stat-value yellow">${stats['Mutation Entered']||0}</div></div>
      <div class="stat-card"><div class="stat-label">Completed</div><div class="stat-value green">${stats.Completed||0}</div></div>
    </div>
    <div class="chart-card">
      <div class="chart-header">
        <div class="chart-title">📈 Monthly File Submissions</div>
      </div>
      <div class="chart-container"><canvas id="trendChart"></canvas></div>
    </div>
    <div class="chart-card">
      <div class="chart-header"><div class="chart-title">📊 Files by Status</div></div>
      <div class="chart-container"><canvas id="statusChart"></canvas></div>
    </div>`;

    // Trend chart
    if(barChart) barChart.destroy();
    const ctx1 = document.getElementById('trendChart').getContext('2d');
    barChart = new Chart(ctx1, {
      type: 'bar',
      data: {
        labels: trend.map(r=>r.month),
        datasets: [{
          label:'Files Submitted',
          data: trend.map(r=>r.cnt),
          backgroundColor:'rgba(31,111,235,.6)',
          borderColor:'rgba(31,111,235,1)',
          borderWidth:1,
          borderRadius:4,
        }]
      },
      options: {
        responsive:true,maintainAspectRatio:false,
        plugins:{legend:{display:false}},
        scales:{
          x:{grid:{color:'rgba(255,255,255,.05)'},ticks:{color:'#7d8590',font:{size:11}}},
          y:{grid:{color:'rgba(255,255,255,.05)'},ticks:{color:'#7d8590',font:{size:11}},beginAtZero:true}
        }
      }
    });

    // Status doughnut
    const ctx2 = document.getElementById('statusChart').getContext('2d');
    const statusLabels = ['Submitted','Verified','Returned','Mutation Entered','Completed'];
    const statusColors = ['#88aaee','#3fb950','#f85149','#d29922','#56d364'];
    new Chart(ctx2, {
      type: 'doughnut',
      data: {
        labels: statusLabels,
        datasets: [{
          data: statusLabels.map(s=>stats[s]||0),
          backgroundColor: statusColors.map(c=>c+'99'),
          borderColor: statusColors,
          borderWidth: 1,
        }]
      },
      options: {
        responsive:true,maintainAspectRatio:false,
        plugins:{
          legend:{position:'right',labels:{color:'#e6edf3',font:{size:12},padding:16}},
        }
      }
    });
  } catch(e) {
    content.innerHTML = `<div style="color:var(--red)">Error: ${e.message}</div>`;
  }
}

// ─── REPORTS (AC/DC/Commissioner/DG) ─────────────────────────────────────────
async function renderReports() {
  setPage('Reports & Analytics', `<button class="btn btn-accent" onclick="exportFilesCSV()">⬇ Export CSV</button>`);
  renderFiles(true); // reuse files table with full view
}

// ─── FILES ───────────────────────────────────────────────────────────────────
let filesData = [];
let fileFilters = {};

async function renderFiles(reportMode=false) {
  if(!reportMode) setPage('File Management',
    me.role==='field_office'
      ? `<button class="btn btn-accent" onclick="openSubmitFile()">+ Submit File</button>`
      : `<button class="btn btn-accent" onclick="exportFilesCSV()">⬇ Export CSV</button>`
  );
  const content = document.getElementById('page-content');
  // Build filter UI
  let geoHtml = await buildGeoFilters();
  content.innerHTML = `
    <div class="filters">
      ${geoHtml}
      <select class="filter-select" id="filter-status" onchange="applyFileFilters()">
        <option value="">All Statuses</option>
        <option>Submitted</option><option>Verified</option><option>Returned</option>
        <option>Mutation Entered</option><option>Completed</option>
      </select>
      <input class="filter-input" id="filter-search" placeholder="Search khewat/mauza/mutation…" oninput="applyFileFilters()" style="width:220px">
    </div>
    <div id="files-table-wrap"></div>`;
  await loadAndRenderFiles();
}

async function buildGeoFilters() {
  const role = me.role;
  let html = '';
  if(['admin','dg'].includes(role)) {
    const states = await api('GET','/states');
    html += `<select class="filter-select" id="filter-state" onchange="onStateFilter()"><option value="">All States</option>${states.map(s=>`<option value="${s.id}">${s.name}</option>`).join('')}</select>`;
    html += `<select class="filter-select" id="filter-division" onchange="onDivisionFilter()"><option value="">All Divisions</option></select>`;
    html += `<select class="filter-select" id="filter-district" onchange="onDistrictFilter()"><option value="">All Districts</option></select>`;
    html += `<select class="filter-select" id="filter-tehsil" onchange="applyFileFilters()"><option value="">All Tehsils</option></select>`;
  } else if(role==='commissioner') {
    const divs = await api('GET','/divisions');
    html += `<select class="filter-select" id="filter-division" onchange="onDivisionFilter()"><option value="">All Divisions</option>${divs.map(d=>`<option value="${d.id}">${d.name}</option>`).join('')}</select>`;
    html += `<select class="filter-select" id="filter-district" onchange="onDistrictFilter()"><option value="">All Districts</option></select>`;
    html += `<select class="filter-select" id="filter-tehsil" onchange="applyFileFilters()"><option value="">All Tehsils</option></select>`;
  } else if(['dc','ac'].includes(role)) {
    const dists = await api('GET','/districts');
    html += `<select class="filter-select" id="filter-district" onchange="onDistrictFilter()"><option value="">All Districts</option>${dists.map(d=>`<option value="${d.id}">${d.name}</option>`).join('')}</select>`;
    html += `<select class="filter-select" id="filter-tehsil" onchange="applyFileFilters()"><option value="">All Tehsils</option></select>`;
  }
  return html;
}

async function onStateFilter() {
  const sid = document.getElementById('filter-state')?.value;
  if(!sid) { resetSelect('filter-division'); resetSelect('filter-district'); resetSelect('filter-tehsil'); return; }
  const divs = await api('GET',`/divisions?state_id=${sid}`);
  populateSelect('filter-division', divs, 'All Divisions', onDivisionFilter);
  resetSelect('filter-district'); resetSelect('filter-tehsil');
  applyFileFilters();
}
async function onDivisionFilter() {
  const did = document.getElementById('filter-division')?.value;
  if(!did) { resetSelect('filter-district'); resetSelect('filter-tehsil'); return applyFileFilters(); }
  const dists = await api('GET',`/districts?division_id=${did}`);
  populateSelect('filter-district', dists, 'All Districts', onDistrictFilter);
  resetSelect('filter-tehsil');
  applyFileFilters();
}
async function onDistrictFilter() {
  const did = document.getElementById('filter-district')?.value;
  if(!did) { resetSelect('filter-tehsil'); return applyFileFilters(); }
  const teh = await api('GET',`/tehsils?district_id=${did}`);
  populateSelect('filter-tehsil', teh, 'All Tehsils', applyFileFilters);
  applyFileFilters();
}
function resetSelect(id) {
  const el = document.getElementById(id);
  if(!el) return;
  el.innerHTML = `<option value="">All ${id.split('-')[1].charAt(0).toUpperCase()+id.split('-')[1].slice(1)}s</option>`;
}
function populateSelect(id, items, placeholder, onchange) {
  const el = document.getElementById(id);
  if(!el) return;
  el.innerHTML = `<option value="">${placeholder}</option>${items.map(i=>`<option value="${i.id}">${i.name}</option>`).join('')}`;
  if(onchange) el.onchange = onchange;
}

function applyFileFilters() {
  loadAndRenderFiles();
}

async function loadAndRenderFiles() {
  const params = new URLSearchParams();
  ['state','division','district','tehsil','status'].forEach(k => {
    const el = document.getElementById(`filter-${k}`);
    if(el?.value) params.append(`${k}_id`, el.value);
  });
  const search = document.getElementById('filter-search')?.value;
  if(search) params.append('search', search);
  const statusEl = document.getElementById('filter-status');
  if(statusEl?.value) params.set('status', statusEl.value);

  try {
    filesData = await api('GET',`/files?${params}`);
    renderFilesTable();
  } catch(e) {
    document.getElementById('files-table-wrap').innerHTML = `<div style="color:var(--red)">Error: ${e.message}</div>`;
  }
}

function statusBadge(s) {
  const cls = {Submitted:'submitted',Verified:'verified',Returned:'returned','Mutation Entered':'mutation',Completed:'completed'};
  return `<span class="badge badge-${cls[s]||'submitted'}">${s}</span>`;
}

function renderFilesTable() {
  const wrap = document.getElementById('files-table-wrap');
  const role = me.role;
  const canEdit = ['arc','admin'].includes(role);
  const canDelete = role==='admin';

  if(!filesData.length) {
    wrap.innerHTML = `<div class="empty-state"><div class="empty-icon">📂</div><p>No files found.</p></div>`;
    return;
  }
  wrap.innerHTML = `
  <div class="table-wrap">
  <table>
    <thead><tr>
      <th>#</th><th>Mauza</th><th>Khewat No</th><th>Status</th>
      <th>Tehsil</th><th>District</th><th>Submitted By</th>
      <th>Date</th><th>Mutation No</th><th>Remarks</th>
      ${canEdit||canDelete?'<th>Actions</th>':''}
    </tr></thead>
    <tbody>
    ${filesData.map((f,i) => `<tr>
      <td class="mono">${f.id}</td>
      <td>${f.mauza_name||'—'}</td>
      <td class="mono">${f.khewat_no}</td>
      <td>${statusBadge(f.status)}</td>
      <td>${f.tehsil_name||'—'}</td>
      <td>${f.district_name||'—'}</td>
      <td>${f.submitted_by_name||'—'}</td>
      <td class="mono">${f.submitted_date||'—'}</td>
      <td class="mono">${f.mutation_no||'—'}</td>
      <td style="max-width:160px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis">${f.remarks||'—'}</td>
      ${canEdit||canDelete ? `<td style="white-space:nowrap">
        ${canEdit?`<button class="btn btn-sm" onclick="openEditFile(${f.id})">Edit</button>`:''}
        ${canDelete?`<button class="btn btn-sm btn-danger" onclick="deleteFile(${f.id})">Del</button>`:''}
      </td>`:''}
    </tr>`).join('')}
    </tbody>
  </table>
  </div>
  <div style="font-size:12px;color:var(--text-muted);margin-top:8px">${filesData.length} file(s) found</div>`;
}

async function openSubmitFile() {
  // Get mauzas for user's tehsil
  let mauzas = [];
  try { mauzas = await api('GET',`/mauzas?tehsil_id=${me.tehsil_id}`); } catch(e){}
  openModal('Submit New File(s)', `
    <div class="form-field">
      <label>Mauza</label>
      <select id="f-mauza">
        <option value="">Select Mauza…</option>
        ${mauzas.map(m=>`<option value="${m.id}">${m.name}</option>`).join('')}
      </select>
    </div>
    <div class="form-field">
      <label>Khewat Numbers <span style="color:var(--text-muted)">(one per line for bulk)</span></label>
      <textarea id="f-khewat" rows="4" placeholder="e.g.\n123\n124\n125"></textarea>
    </div>
    <div class="form-field">
      <label>Date</label>
      <input type="date" id="f-date" value="${new Date().toISOString().split('T')[0]}">
    </div>
    <div class="form-field">
      <label>Remarks</label>
      <textarea id="f-remarks" rows="2" placeholder="Optional remarks…"></textarea>
    </div>
    <div class="modal-footer">
      <button class="btn" onclick="closeModal()">Cancel</button>
      <button class="btn btn-accent" onclick="submitFiles()">Submit</button>
    </div>
  `);
}

async function submitFiles() {
  const mauzaId = document.getElementById('f-mauza').value;
  const khewats = document.getElementById('f-khewat').value.split('\n').map(s=>s.trim()).filter(Boolean);
  const date = document.getElementById('f-date').value;
  const remarks = document.getElementById('f-remarks').value;
  if(!mauzaId||!khewats.length) return toast('error','Fill in all required fields');
  try {
    const files = khewats.map(k=>({mauza_id:mauzaId,khewat_no:k,submitted_date:date,remarks}));
    await api('POST','/files',{files});
    closeModal();
    toast('success',`${files.length} file(s) submitted`);
    loadAndRenderFiles();
  } catch(e) { toast('error',e.message); }
}

async function openEditFile(id) {
  const f = filesData.find(x=>x.id===id);
  if(!f) return;
  const role = me.role;
  // Determine allowed transitions
  let statusOptions = '';
  if(role==='arc') {
    const transitions = ['Verified','Returned','Mutation Entered','Completed'];
    statusOptions = transitions.map(s=>`<option value="${s}" ${f.status===s?'selected':''}>${s}</option>`).join('');
  } else if(['admin'].includes(role)) {
    ['Submitted','Verified','Returned','Mutation Entered','Completed'].forEach(s=>{
      statusOptions += `<option value="${s}" ${f.status===s?'selected':''}>${s}</option>`;
    });
  }

  openModal(`Update File #${f.id}`, `
    <div style="margin-bottom:16px;padding:12px;background:var(--bg);border-radius:6px;border:1px solid var(--border)">
      <div style="font-size:11px;color:var(--text-muted);margin-bottom:6px">FILE DETAILS</div>
      <div style="font-size:13px"><b>Mauza:</b> ${f.mauza_name} | <b>Khewat:</b> ${f.khewat_no}</div>
      <div style="font-size:13px;margin-top:4px"><b>Current Status:</b> ${statusBadge(f.status)}</div>
    </div>
    ${statusOptions ? `<div class="form-field"><label>New Status</label><select id="e-status">${statusOptions}</select></div>` : ''}
    <div class="form-field">
      <label>Mutation Number</label>
      <input type="text" id="e-mutation" value="${f.mutation_no||''}" placeholder="Enter mutation no…">
    </div>
    <div class="form-field">
      <label>Remarks</label>
      <textarea id="e-remarks" rows="3">${f.remarks||''}</textarea>
    </div>
    <div class="modal-footer">
      <button class="btn" onclick="closeModal()">Cancel</button>
      <button class="btn btn-accent" onclick="updateFile(${f.id})">Update</button>
    </div>
  `);
}

async function updateFile(id) {
  const status = document.getElementById('e-status')?.value;
  const mutation_no = document.getElementById('e-mutation').value.trim();
  const remarks = document.getElementById('e-remarks').value.trim();
  try {
    await api('PUT',`/files/${id}`,{status,mutation_no,remarks});
    closeModal();
    toast('success','File updated');
    loadAndRenderFiles();
  } catch(e) { toast('error',e.message); }
}

async function deleteFile(id) {
  if(!confirm('Delete this file?')) return;
  try {
    await api('DELETE',`/files/${id}`);
    toast('success','File deleted');
    loadAndRenderFiles();
  } catch(e) { toast('error',e.message); }
}

function exportFilesCSV() {
  if(!filesData.length) return toast('info','No data to export');
  const cols = ['id','mauza_name','khewat_no','status','tehsil_name','district_name','division_name','state_name','submitted_by_name','submitted_date','mutation_no','remarks'];
  const header = cols.join(',');
  const rows = filesData.map(f => cols.map(c=>JSON.stringify(f[c]??'')).join(','));
  const csv = [header,...rows].join('\n');
  const blob = new Blob([csv],{type:'text/csv'});
  const a = document.createElement('a'); a.href=URL.createObjectURL(blob);
  a.download=`files_export_${Date.now()}.csv`; a.click();
}

// ─── GEOGRAPHY (ADMIN) ───────────────────────────────────────────────────────
async function renderGeography() {
  setPage('Geographic Hierarchy',`<button class="btn btn-accent" onclick="openAddGeo()">+ Add Entry</button>`);
  const content = document.getElementById('page-content');
  content.innerHTML = `
    <div style="display:grid;grid-template-columns:1fr 1fr;gap:16px;margin-bottom:20px">
      <div>
        <div class="section-title">States</div>
        <div id="geo-states-wrap"></div>
      </div>
      <div>
        <div class="section-title">Divisions</div>
        <div id="geo-divisions-wrap"></div>
      </div>
    </div>
    <div style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:16px">
      <div><div class="section-title">Districts</div><div id="geo-districts-wrap"></div></div>
      <div><div class="section-title">Tehsils</div><div id="geo-tehsils-wrap"></div></div>
      <div><div class="section-title">Mauzas</div><div id="geo-mauzas-wrap"></div></div>
    </div>`;
  loadGeoData();
}

async function loadGeoData() {
  try {
    const [states,divs,dists,teh,mz] = await Promise.all([
      api('GET','/states'), api('GET','/divisions'), api('GET','/districts'),
      api('GET','/tehsils'), api('GET','/mauzas')
    ]);
    renderGeoList('geo-states-wrap', states, 'state', r=>r.name, r=>r.name);
    renderGeoList('geo-divisions-wrap', divs, 'division', r=>r.name, r=>`${r.name} <span style="color:var(--text-muted);font-size:11px">/ ${r.state_name}</span>`);
    renderGeoList('geo-districts-wrap', dists, 'district', r=>r.name, r=>`${r.name} <span style="color:var(--text-muted);font-size:11px">/ ${r.division_name}</span>`);
    renderGeoList('geo-tehsils-wrap', teh, 'tehsil', r=>r.name, r=>`${r.name} <span style="color:var(--text-muted);font-size:11px">/ ${r.district_name}</span>`);
    renderGeoList('geo-mauzas-wrap', mz, 'mauza', r=>r.name, r=>`${r.name} <span style="color:var(--text-muted);font-size:11px">/ ${r.tehsil_name}</span>`);
  } catch(e) { toast('error',e.message); }
}

function renderGeoList(wrapperId, items, type, nameFunc, htmlFunc) {
  const wrap = document.getElementById(wrapperId);
  if(!wrap) return;
  if(!items.length) { wrap.innerHTML='<div style="color:var(--text-muted);font-size:13px">None</div>'; return; }
  wrap.innerHTML = `<div class="table-wrap"><table>
    <tbody>${items.map(r=>`<tr>
      <td style="font-size:13px">${htmlFunc(r)}</td>
      <td style="width:80px;white-space:nowrap">
        <button class="btn btn-sm" onclick="editGeo('${type}',${r.id},'${nameFunc(r).replace(/'/g,"\\'")}')">✎</button>
        <button class="btn btn-sm btn-danger" onclick="deleteGeo('${type}',${r.id})">✕</button>
      </td>
    </tr>`).join('')}</tbody>
  </table></div>`;
}

async function openAddGeo() {
  let states=[], divs=[], dists=[], teh=[];
  try { [states,divs,dists,teh] = await Promise.all([
    api('GET','/states'),api('GET','/divisions'),api('GET','/districts'),api('GET','/tehsils')
  ]); } catch(e){}

  openModal('Add Geographic Entries', `
    <div class="form-field">
      <label>Level</label>
      <select id="geo-level" onchange="updateGeoParentField()">
        <option value="state">State</option>
        <option value="division">Division</option>
        <option value="district">District</option>
        <option value="tehsil">Tehsil</option>
        <option value="mauza">Mauza</option>
      </select>
    </div>
    <div id="geo-parent-field"></div>
    <div class="form-field">
      <label>Names <span style="color:var(--text-muted)">(one per line for bulk)</span></label>
      <textarea id="geo-names" rows="5" placeholder="e.g.\nLahore Division\nRawalpindi Division"></textarea>
    </div>
    <div class="modal-footer">
      <button class="btn" onclick="closeModal()">Cancel</button>
      <button class="btn btn-accent" onclick="createGeoEntries()">Create</button>
    </div>
  `);
  // Store data for parent selects
  window._geoData = {states,divs,dists,teh};
  updateGeoParentField();
}

function updateGeoParentField() {
  const level = document.getElementById('geo-level')?.value;
  const {states=[],divs=[],dists=[],teh=[]} = window._geoData||{};
  const field = document.getElementById('geo-parent-field');
  if(!field) return;
  if(level==='state') { field.innerHTML=''; return; }
  let opts='', label='';
  if(level==='division') { opts=states.map(s=>`<option value="${s.id}">${s.name}</option>`).join(''); label='State'; }
  if(level==='district') { opts=divs.map(d=>`<option value="${d.id}">${d.name}</option>`).join(''); label='Division'; }
  if(level==='tehsil') { opts=dists.map(d=>`<option value="${d.id}">${d.name}</option>`).join(''); label='District'; }
  if(level==='mauza') { opts=teh.map(t=>`<option value="${t.id}">${t.name}</option>`).join(''); label='Tehsil'; }
  field.innerHTML = `<div class="form-field"><label>Parent ${label}</label><select id="geo-parent">${opts}</select></div>`;
}

async function createGeoEntries() {
  const level = document.getElementById('geo-level').value;
  const names = document.getElementById('geo-names').value.split('\n').map(s=>s.trim()).filter(Boolean);
  const parentId = document.getElementById('geo-parent')?.value;
  if(!names.length) return toast('error','Enter at least one name');
  const parentKey = {division:'state_id',district:'division_id',tehsil:'district_id',mauza:'tehsil_id'}[level];
  const body = {names};
  if(parentKey) body[parentKey] = parentId;
  try {
    await api('POST',`/${level}s`, body);
    closeModal();
    toast('success',`${names.length} ${level}(s) created`);
    loadGeoData();
  } catch(e) { toast('error',e.message); }
}

async function editGeo(type, id, currentName) {
  openModal(`Edit ${type.charAt(0).toUpperCase()+type.slice(1)}`, `
    <div class="form-field">
      <label>Name</label>
      <input type="text" id="edit-geo-name" value="${currentName}">
    </div>
    <div class="modal-footer">
      <button class="btn" onclick="closeModal()">Cancel</button>
      <button class="btn btn-accent" onclick="saveGeoEdit('${type}',${id})">Save</button>
    </div>
  `);
}

async function saveGeoEdit(type, id) {
  const name = document.getElementById('edit-geo-name').value.trim();
  if(!name) return toast('error','Name required');
  try {
    // Need parent id for update - fetch current record
    const items = await api('GET',`/${type}s`);
    const item = items.find(i=>i.id===id);
    const parentKey = {division:'state_id',district:'division_id',tehsil:'district_id',mauza:'tehsil_id'}[type];
    const body = {name};
    if(parentKey && item) body[parentKey] = item[parentKey];
    await api('PUT',`/${type}s/${id}`, body);
    closeModal();
    toast('success','Updated');
    loadGeoData();
  } catch(e) { toast('error',e.message); }
}

async function deleteGeo(type, id) {
  if(!confirm(`Delete this ${type}? All child records will also be deleted.`)) return;
  try {
    await api('DELETE',`/${type}s/${id}`);
    toast('success','Deleted');
    loadGeoData();
  } catch(e) { toast('error',e.message); }
}

// ─── USERS (ADMIN) ───────────────────────────────────────────────────────────
let usersData = [];
async function renderUsers() {
  setPage('User Management',`<button class="btn btn-accent" onclick="openAddUser()">+ Add User</button>`);
  try {
    usersData = await api('GET','/users');
    const content = document.getElementById('page-content');
    if(!usersData.length) {
      content.innerHTML=`<div class="empty-state"><div class="empty-icon">👥</div><p>No users found.</p></div>`;
      return;
    }
    content.innerHTML = `<div class="table-wrap"><table>
      <thead><tr><th>#</th><th>Username</th><th>Role</th><th>State</th><th>Division</th><th>District</th><th>Tehsil</th><th>Created</th><th>Actions</th></tr></thead>
      <tbody>${usersData.map(u=>`<tr>
        <td class="mono">${u.id}</td>
        <td>${u.username}</td>
        <td><span class="role-chip role-${u.role}">${roleLabel(u.role)}</span></td>
        <td>${u.state_name||'—'}</td>
        <td>${u.division_name||'—'}</td>
        <td>${u.district_name||'—'}</td>
        <td>${u.tehsil_name||'—'}</td>
        <td class="mono" style="font-size:11px">${(u.created_at||'').split('T')[0]}</td>
        <td style="white-space:nowrap">
          <button class="btn btn-sm" onclick="openEditUser(${u.id})">Edit</button>
          <button class="btn btn-sm btn-danger" onclick="deleteUser(${u.id})">Del</button>
        </td>
      </tr>`).join('')}</tbody>
    </table></div>`;
  } catch(e) { toast('error',e.message); }
}

async function openAddUser() {
  const geoSelects = await buildUserGeoSelects();
  openModal('Add New User', `
    <div class="form-row">
      <div class="form-field">
        <label>Username</label>
        <input type="text" id="u-username" placeholder="Username">
      </div>
      <div class="form-field">
        <label>Password</label>
        <input type="password" id="u-password" placeholder="Password">
      </div>
    </div>
    <div class="form-field">
      <label>Role</label>
      <select id="u-role">
        <option value="field_office">Field Office</option>
        <option value="arc">ARC</option>
        <option value="ac">AC</option>
        <option value="dc">DC</option>
        <option value="commissioner">Commissioner</option>
        <option value="dg">DG</option>
        <option value="admin">Admin</option>
      </select>
    </div>
    ${geoSelects}
    <div class="modal-footer">
      <button class="btn" onclick="closeModal()">Cancel</button>
      <button class="btn btn-accent" onclick="createUser()">Create</button>
    </div>
  `);
}

async function buildUserGeoSelects(user=null) {
  let states=[],divs=[],dists=[],teh=[];
  try { [states,divs,dists,teh]=await Promise.all([
    api('GET','/states'),api('GET','/divisions'),api('GET','/districts'),api('GET','/tehsils')
  ]); } catch(e){}
  const sel = (id,opts,label,val=null)=>`<div class="form-field"><label>${label}</label><select id="${id}">
    <option value="">None</option>${opts.map(o=>`<option value="${o.id}"${val&&o.id==val?' selected':''}>${o.name}</option>`).join('')}
  </select></div>`;
  return `<div class="form-row">
    ${sel('u-state',states,'State',user?.state_id)}
    ${sel('u-division',divs,'Division',user?.division_id)}
  </div><div class="form-row">
    ${sel('u-district',dists,'District',user?.district_id)}
    ${sel('u-tehsil',teh,'Tehsil',user?.tehsil_id)}
  </div>`;
}

async function createUser() {
  const body = {
    username: document.getElementById('u-username').value.trim(),
    password: document.getElementById('u-password').value,
    role: document.getElementById('u-role').value,
    state_id: document.getElementById('u-state')?.value||null,
    division_id: document.getElementById('u-division')?.value||null,
    district_id: document.getElementById('u-district')?.value||null,
    tehsil_id: document.getElementById('u-tehsil')?.value||null,
  };
  if(!body.username||!body.password) return toast('error','Username and password required');
  try {
    await api('POST','/users',body);
    closeModal();
    toast('success','User created');
    renderUsers();
  } catch(e) { toast('error',e.message); }
}

async function openEditUser(id) {
  const u = usersData.find(x=>x.id===id);
  if(!u) return;
  const geoSelects = await buildUserGeoSelects(u);
  openModal(`Edit User: ${u.username}`, `
    <div class="form-row">
      <div class="form-field">
        <label>Username</label>
        <input type="text" id="u-username" value="${u.username}">
      </div>
      <div class="form-field">
        <label>New Password <span style="color:var(--text-muted)">(leave blank to keep)</span></label>
        <input type="password" id="u-password" placeholder="New password…">
      </div>
    </div>
    <div class="form-field">
      <label>Role</label>
      <select id="u-role">
        ${['field_office','arc','ac','dc','commissioner','dg','admin'].map(r=>`<option value="${r}"${u.role===r?' selected':''}>${roleLabel(r)}</option>`).join('')}
      </select>
    </div>
    ${geoSelects}
    <div class="modal-footer">
      <button class="btn" onclick="closeModal()">Cancel</button>
      <button class="btn btn-accent" onclick="saveUser(${id})">Save</button>
    </div>
  `);
}

async function saveUser(id) {
  const body = {
    username: document.getElementById('u-username').value.trim(),
    password: document.getElementById('u-password').value||undefined,
    role: document.getElementById('u-role').value,
    state_id: document.getElementById('u-state')?.value||null,
    division_id: document.getElementById('u-division')?.value||null,
    district_id: document.getElementById('u-district')?.value||null,
    tehsil_id: document.getElementById('u-tehsil')?.value||null,
  };
  try {
    await api('PUT',`/users/${id}`,body);
    closeModal();
    toast('success','User updated');
    renderUsers();
  } catch(e) { toast('error',e.message); }
}

async function deleteUser(id) {
  if(!confirm('Delete this user?')) return;
  try {
    await api('DELETE',`/users/${id}`);
    toast('success','User deleted');
    renderUsers();
  } catch(e) { toast('error',e.message); }
}

// ─── UTILS ───────────────────────────────────────────────────────────────────
function setPage(title, actionsHtml) {
  document.getElementById('page-title').textContent = title;
  document.getElementById('topbar-actions').innerHTML = actionsHtml||'';
}

function openModal(title, bodyHtml) {
  document.getElementById('modal-title').textContent = title;
  document.getElementById('modal-body').innerHTML = bodyHtml;
  document.getElementById('modal-overlay').classList.add('open');
}
function closeModal() {
  document.getElementById('modal-overlay').classList.remove('open');
}
document.getElementById('modal-overlay').addEventListener('click', e => {
  if(e.target===document.getElementById('modal-overlay')) closeModal();
});

function toast(type, msg, duration=3500) {
  const tc = document.getElementById('toast-container');
  const div = document.createElement('div');
  const icons = {success:'✓',error:'✕',info:'ℹ'};
  div.className = `toast ${type}`;
  div.innerHTML = `<span>${icons[type]||'•'}</span><span>${msg}</span>`;
  tc.appendChild(div);
  setTimeout(()=>div.remove(), duration);
}

// Init
initApp();
</script>
</body>
</html>"""

@app.route('/')
def index():
    return render_template_string(HTML)

if __name__ == '__main__':
    init_db()
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
