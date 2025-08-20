import os, time, requests
from flask import Flask, redirect, request, session, jsonify
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)
app.secret_key = "change_me_en_prod"

CLIENT_ID = os.getenv("CLIENT_ID")
CLIENT_SECRET = os.getenv("CLIENT_SECRET")
REDIRECT_URI = os.getenv("REDIRECT_URI")
VERIFY_TOKEN = os.getenv("VERIFY_TOKEN")

# Démo : stockage très simple en mémoire (à remplacer par une DB en prod)
TOKENS_BY_ATHLETE = {}  # {athlete_id: {"access_token":..., "refresh_token":..., "expires_at":...}}

@app.route("/")
def index():
    return '<a href="/login">Se connecter avec Strava</a> | <a href="/activities">Voir mes activités</a>'

@app.route("/login")
def login():
    auth_url = (
        "https://www.strava.com/oauth/authorize"
        f"?client_id={CLIENT_ID}"
        f"&redirect_uri={REDIRECT_URI}"
        f"&response_type=code"
        f"&scope=read,activity:read_all"
    )
    return redirect(auth_url)

@app.route("/callback")
def callback():
    code = request.args.get("code")
    resp = requests.post("https://www.strava.com/oauth/token", data={
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "code": code,
        "grant_type": "authorization_code",
    })
    data = resp.json()

    # Stocke en session pour l'utilisateur connecté
    session["athlete_id"] = data["athlete"]["id"]
    session["access_token"] = data["access_token"]
    session["refresh_token"] = data["refresh_token"]
    session["expires_at"] = data["expires_at"]

    # Stocke aussi côté serveur (pour le webhook)
    TOKENS_BY_ATHLETE[data["athlete"]["id"]] = {
        "access_token": data["access_token"],
        "refresh_token": data["refresh_token"],
        "expires_at": data["expires_at"],
    }
    return "Connecté ✅ — <a href='/athlete'>Mon profil</a> | <a href='/activities'>Mes activités</a>"

def get_access_token_for(athlete_id):
    """Retourne un access_token valide pour un athlète, et rafraîchit si besoin."""
    t = TOKENS_BY_ATHLETE.get(athlete_id)
    if not t:
        return None
    if time.time() > t["expires_at"]:
        r = requests.post("https://www.strava.com/oauth/token", data={
            "client_id": CLIENT_ID,
            "client_secret": CLIENT_SECRET,
            "grant_type": "refresh_token",
            "refresh_token": t["refresh_token"],
        }).json()
        TOKENS_BY_ATHLETE[athlete_id] = {
            "access_token": r["access_token"],
            "refresh_token": r["refresh_token"],
            "expires_at": r["expires_at"],
        }
        return r["access_token"]
    return t["access_token"]

def get_my_access_token():
    """Version session pour l'utilisateur courant (confort)."""
    athlete_id = session.get("athlete_id")
    if not athlete_id:
        return None
    # aligne le store mémoire sur la session
    TOKENS_BY_ATHLETE.setdefault(athlete_id, {
        "access_token": session["access_token"],
        "refresh_token": session["refresh_token"],
        "expires_at": session["expires_at"],
    })
    return get_access_token_for(athlete_id)

@app.route("/athlete")
def athlete():
    token = get_my_access_token()
    if not token: return "Pas connecté", 401
    r = requests.get("https://www.strava.com/api/v3/athlete",
                     headers={"Authorization": f"Bearer {token}"})
    return r.json()

@app.route("/activities")
def activities():
    token = get_my_access_token()
    if not token: return "Pas connecté", 401
    r = requests.get("https://www.strava.com/api/v3/athlete/activities",
                     headers={"Authorization": f"Bearer {token}"},
                     params={"per_page": 10})
    return r.json()

# WEBHOOK (vérif + réception d'événements)
@app.route("/strava-webhook", methods=["GET", "POST"])
def strava_webhook():
    if request.method == "GET":
        # Validation initiale par Strava
        if request.args.get("hub.mode") == "subscribe" and request.args.get("hub.verify_token") == VERIFY_TOKEN:
            return jsonify({"hub.challenge": request.args.get("hub.challenge")})
        return "Verification failed", 403

    # Réception d'un événement
    evt = request.get_json(force=True)
    # Exemple minimal : si nouvelle activité, on va chercher ses détails
    if evt.get("object_type") == "activity" and evt.get("aspect_type") == "create":
        athlete_id = evt.get("owner_id")
        activity_id = evt.get("object_id")
        token = get_access_token_for(athlete_id)
        if token:
            detail = requests.get(f"https://www.strava.com/api/v3/activities/{activity_id}",
                                  headers={"Authorization": f"Bearer {token}"}).json()
            print("Nouvelle activité 📩:", {"athlete": athlete_id, "id": activity_id,
                                            "name": detail.get("name"), "distance": detail.get("distance")})
        else:
            print("⚠️ Pas de token pour l'athlète", athlete_id)
    return "", 200

if __name__ == "__main__":
    app.run(port=5000, debug=True)
