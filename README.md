# MS MOTORS — Étiquettes Chronopost

Outil interne pour ajouter automatiquement les articles commandés sur les étiquettes Chronopost.

## Déploiement sur Railway (gratuit, 5 min)

### 1. Créer un compte Railway
→ https://railway.app (connexion avec GitHub)

### 2. Mettre le code sur GitHub
```bash
git init
git add .
git commit -m "first commit"
git branch -M main
git remote add origin https://github.com/TON_USER/chronopost-app.git
git push -u origin main
```

### 3. Déployer sur Railway
1. Sur railway.app → **New Project** → **Deploy from GitHub repo**
2. Sélectionne ton repo `chronopost-app`
3. Railway détecte automatiquement le `Procfile` et déploie
4. Dans **Settings → Domains** → **Generate Domain** pour obtenir ton URL publique

✅ C'est tout ! Tu as une URL du type `https://chronopost-app-xxxx.up.railway.app`

---

## Alternative : Render.com (aussi gratuit)

1. → https://render.com → New Web Service
2. Connecte ton repo GitHub
3. Build command: `pip install -r requirements.txt`
4. Start command: `gunicorn app:app --bind 0.0.0.0:$PORT --timeout 120`

---

## Lancer en local

```bash
pip install -r requirements.txt
python app.py
# → http://localhost:5000
```
