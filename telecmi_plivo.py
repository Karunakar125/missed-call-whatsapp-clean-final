import time
import requests
import logging
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response

app = FastAPI()

# ==============================
# LOGGING
# ==============================
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ==============================
# CONFIG
# ==============================
ACCESS_TOKEN = "EAANOcn0m1scBRXNcKLZCZAQX2ZAfAQ7n29GvZBXGT9c3ZACmv0hHQrMlJn7feUPBlSHUt7KrklCec6GAaYnHIA20yOlwd6aer5DzZAEidNZB45I1EZCnHi9ZB8LbFt7rarZAaypsIkSyEq0ZBWndVH3CtAgAjxtdTAZCrrOGPqtLmNDUEfzVeeC6CoV7mSj230bQUtjYeZCu23OULcVvtJEtG48P0nYUS4g8r69M8DqNKcZAMRV7XkDKB9nDUzAomPaWlTgl66PTBI8nLQWAGAr4hv8kir"
#os.getenv("WHATSAPP_TOKEN") remove the above token and use this method to use your own Access Token which is saved in ,env file.
PHONE_NUMBER_ID = "1072511745947523" # os.getenv("PHONE_NUMBER_ID")

AGENT = {
    "name": "Karunakar",
    "phone": "91XXXXXXXX",
    "whatsapp": "91XXXXXXX",
    "city": "Bangalore",
}

# ==============================
# MEMORY
# ==============================
processed_calls = {}
last_sent = {}

# ==============================
# HELPERS
# ==============================
def normalize_number(number: str):
    if not number:
        return None
    number = str(number).replace("+", "").replace(" ", "")
    if len(number) == 10:
        return "91" + number
    return number


def is_valid_indian_number(number):
    return number and number.startswith("91") and len(number) == 12


def should_send(phone):
    now = time.time()
    if phone in last_sent and now - last_sent[phone] < 300:
        return False
    last_sent[phone] = now
    return True


def is_duplicate_call(call_id):
    now = time.time()

    # cleanup old entries
    for k in list(processed_calls.keys()):
        if now - processed_calls[k] > 600:
            del processed_calls[k]

    if call_id in processed_calls:
        return True

    processed_calls[call_id] = now
    return False


# ==============================
# WHATSAPP → CUSTOMER
# ==============================
def send_customer_whatsapp(phone):
    if not is_valid_indian_number(phone):
        logger.warning(f"Invalid phone: {phone}")
        return

    url = f"https://graph.facebook.com/v25.0/{PHONE_NUMBER_ID}/messages"

    headers = {
        "Authorization": f"Bearer {ACCESS_TOKEN}",
        "Content-Type": "application/json"
    }

    payload = {
        "messaging_product": "whatsapp",
        "to": phone,
        "type": "template",
        "template": {
            "name": "missed_call_buy_intent_v1",
            "language": {"code": "en"}
        }
    }

    try:
        res = requests.post(url, headers=headers, json=payload)
        logger.info(f"Customer WA: {res.status_code} | {res.text}")
    except Exception as e:
        logger.error(f"Customer WA Error: {str(e)}")


# ==============================
# WHATSAPP → AGENT
# ==============================
def send_agent_whatsapp(customer_phone):
    url = f"https://graph.facebook.com/v25.0/{PHONE_NUMBER_ID}/messages"

    headers = {
        "Authorization": f"Bearer {ACCESS_TOKEN}",
        "Content-Type": "application/json"
    }

    payload = {
        "messaging_product": "whatsapp",
        "to": AGENT["whatsapp"],
        "type": "template",
        "template": {
            "name": "agent_missed_call_alert_v1",
            "language": {"code": "en_US"},
            "components": [
                {
                    "type": "body",
                    "parameters": [
                        {"type": "text", "text": customer_phone}
                    ]
                }
            ]
        }
    }

    try:
        res = requests.post(url, headers=headers, json=payload)
        logger.info(f"Agent WA: {res.status_code} | {res.text}")
    except Exception as e:
        logger.error(f"Agent WA Error: {str(e)}")


# ==============================
# TELECMI WEBHOOK (UNCHANGED WORKING LOGIC)
# ==============================
@app.post("/incoming-call")
async def incoming_call(request: Request):
    try:
        try:
            data = await request.json()
        except:
            form = await request.form()
            data = dict(form)

        logger.info(f"TELECMI Payload: {data}")

        if data.get("type") != "cdr":
            return JSONResponse({"status": "ignored"})

        caller = normalize_number(data.get("from"))
        waitedsec = int(data.get("waitedsec", 0))
        call_id = data.get("conversation_uuid")

        if is_duplicate_call(call_id):
            return JSONResponse({"status": "duplicate"})

        if waitedsec >= 10:
            logger.info("🔥 TELECMI VALID LEAD")

            if should_send(caller):
                send_customer_whatsapp(caller)
                time.sleep(2)
                send_agent_whatsapp(caller)
            else:
                logger.info("Cooldown active")

        else:
            logger.info("⚠️ LOW INTENT")

        return JSONResponse({"status": "processed"})

    except Exception as e:
        logger.error(str(e))
        return JSONResponse({"status": "error"}, status_code=500)


# ==============================
# PLIVO WEBHOOK (FINAL FIXED LOGIC)
# ==============================
@app.post("/voice")
async def plivo_voice(request: Request):
    try:
        form = await request.form()
        data = dict(form)

        logger.info(f"PLIVO Payload: {data}")

        call_id = data.get("CallUUID")
        event = data.get("Event")
        hangup_cause = (data.get("HangupCause") or "").upper()
        call_status = (data.get("CallStatus") or "").lower()
        caller = normalize_number(data.get("From"))

        # ❌ DO NOT CHECK DUPLICATE HERE

        # ✅ ONLY process Hangup
        if event != "Hangup":
            logger.info(f"Ignored → Event: {event}")
            return Response("<Response></Response>", media_type="application/xml")

        # ✅ NOW check duplicate (after hangup only)
        if is_duplicate_call(call_id):
            logger.info("Duplicate hangup ignored")
            return Response("<Response></Response>", media_type="application/xml")

        # ✅ FINAL MISSED CALL LOGIC
        if (
            call_status in ["busy", "no-answer", "failed"] or
            hangup_cause in ["USER_BUSY", "NO_ANSWER", "ORIGINATOR_CANCEL"]
        ):
            logger.info(f"🔥 VALID LEAD | Status: {call_status} | Cause: {hangup_cause}")

            if should_send(caller):
                send_customer_whatsapp(caller)
                time.sleep(2)
                send_agent_whatsapp(caller)
            else:
                logger.info("Cooldown active")

        else:
            logger.info(f"Ignored Hangup → Status: {call_status}, Cause: {hangup_cause}")

        return Response("<Response></Response>", media_type="application/xml")

    except Exception as e:
        logger.error(f"Plivo Error: {str(e)}")
        return Response("<Response></Response>", media_type="application/xml")
# ==============================
# HEALTH CHECK
# ==============================
@app.get("/")
def health():
    return {"status": "running"}
    
    #python -m uvicorn telecmi_plivo:app --reload --port 8000
    #ngrok http 8000