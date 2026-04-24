# 🗂 Land Record & File Tracking System (LRFTS)

A complete, single-file Land Record and File Tracking System built with Flask + SQLite.
Minimal architecture — just **one Python file** + requirements.

---

## 📁 Files
```
app.py            ← Entire backend + frontend (single file)
requirements.txt  ← Python dependencies
Procfile          ← For Heroku / Railway
render.yaml       ← For Render.com
```

---

## 🚀 Local Run

```bash
pip install -r requirements.txt
python app.py
# Visit http://localhost:5000
```

---

## ☁️ Deploy to Render.com (FREE)

1. Push files to a GitHub repo
2. Go to [render.com](https://render.com) → New Web Service
3. Connect your repo
4. Render auto-detects `render.yaml` — click **Deploy**
5. Done ✓

## ☁️ Deploy to Railway.app (FREE)

```bash
railway login
railway init
railway up
```

## ☁️ Deploy to Heroku

```bash
heroku create your-app-name
git push heroku main
heroku open
```

---

## 👤 Default Login Credentials

| Username   | Password    | Role           | Scope         |
|------------|-------------|----------------|---------------|
| admin      | admin123    | Admin          | Everything    |
| field1     | field123    | Field Office   | Shalimar Tehsil |
| arc1       | arc123      | ARC            | Shalimar Tehsil |
| ac1        | ac123       | AC             | Lahore District |
| dc1        | dc123       | DC             | Lahore District |
| comm1      | comm123     | Commissioner   | Lahore Division |
| dg1        | dg123       | DG (Director General) | Punjab State |

> **Change all passwords immediately after first login!**

---

## 🏗 Architecture

### Geographic Hierarchy
```
State → Division → District → Tehsil → Mauza
```

### File Lifecycle (Workflow)
```
[Field Office] Submitted
      ↓
[ARC] Verified ──→ Returned (back to Field Office)
      ↓
[ARC] Mutation Entered
      ↓
[ARC] Completed ✓
```

### Role Permissions

| Action                  | field_office | arc | ac | dc | commissioner | dg | admin |
|-------------------------|:---:|:---:|:---:|:---:|:---:|:---:|:---:|
| Submit files            | ✓   |     |     |     |              |    | ✓    |
| View own files          | ✓   |     |     |     |              |    | ✓    |
| Verify / Return files   |     | ✓   |     |     |              |    | ✓    |
| Enter Mutation No       |     | ✓   |     |     |              |    | ✓    |
| Mark Completed          |     | ✓   |     |     |              |    | ✓    |
| View district reports   |     |     | ✓   | ✓   |              |    | ✓    |
| View division reports   |     |     |     |     | ✓            |    | ✓    |
| View state reports      |     |     |     |     |              | ✓  | ✓    |
| Manage users            |     |     |     |     |              |    | ✓    |
| Manage geography        |     |     |     |     |              |    | ✓    |
| Export CSV              | —   | —   | ✓   | ✓   | ✓            | ✓  | ✓    |

### Data Isolation
- **Field Office & ARC**: Restricted to their assigned Tehsil only
- **AC & DC**: Can see their District only
- **Commissioner**: Can see their Division only
- **DG**: Can see their State only
- **Admin**: Sees everything

---

## 🔒 Security Notes

- Passwords are SHA-256 hashed (upgrade to bcrypt for production)
- Session-based authentication with server-side secret key
- Role-based access control enforced on every API endpoint
- Geographic scope enforced server-side (cannot be bypassed client-side)
- Set `SECRET_KEY` environment variable in production

---

## 🗄 Database Schema

```sql
states(id, name)
divisions(id, name, state_id)
districts(id, name, division_id)
tehsils(id, name, district_id)
mauzas(id, name, tehsil_id)

users(id, username, password, role,
      state_id, division_id, district_id, tehsil_id)

files(id, mauza_id, khewat_no, status,
      submitted_by, submitted_date, remarks,
      mutation_no, tehsil_id, district_id,
      division_id, state_id, updated_at, updated_by)
```

---

## ⚡ Bulk Operations

**Bulk file submission** (Field Office):
- Enter multiple Khewat numbers, one per line
- All submitted in one click

**Bulk geography creation** (Admin):
- Enter multiple names, one per line
- Select parent level once

**Bulk user creation** (Admin API):
```json
POST /api/users
{"users": [{"username":"u1","password":"p1","role":"arc","tehsil_id":1}, ...]}
```
