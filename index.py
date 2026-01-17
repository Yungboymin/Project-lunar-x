import os
import asyncio
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from telethon import TelegramClient, errors
from telethon.sessions import StringSession
from motor.motor_asyncio import AsyncIOMotorClient

app = FastAPI()

# --- YOUR CREDENTIALS ---
API_ID = 36531006
API_HASH = '8b4df3bdc80ff44b80a1d788d4e55eb2'
MONGO_URI = "mongodb+srv://eternlxz516_db_user:1asJy8YrLKj4cL73@lunar.6ltkilo.mongodb.net/?appName=Lunar"

db_client = AsyncIOMotorClient(MONGO_URI)
db = db_client["Lunar"]
temp_auth = db["temp_auth"]
final_sessions = db["sessions"]

class PhoneRequest(BaseModel):
    phone: str
    user_id: str | None = "unknown"

class VerifyRequest(BaseModel):
    user_id: str | None = "unknown"
    code: str
    password: str | None = None

@app.post("/send_code")
async def send_code(req: PhoneRequest):
    client = TelegramClient(StringSession(), API_ID, API_HASH)
    await client.connect()
    try:
        # Request code from Telegram
        sent_code = await client.send_code_request(req.phone)
        
        # EXPLICIT STEP: Save the hash. Without this, /verify cannot work on Vercel.
        await temp_auth.update_one(
            {"user_id": req.user_id},
            {"$set": {
                "phone": req.phone, 
                "phone_code_hash": sent_code.phone_code_hash
            }},
            upsert=True
        )
        return {"status": "success"}
    except errors.FloodWaitError as e:
        return {"status": "error", "message": f"Wait {e.seconds} seconds (Flood limit)."}
    except Exception as e:
        return {"status": "error", "message": str(e)}
    finally:
        await client.disconnect()

@app.post("/verify")
async def verify(req: VerifyRequest):
    # 1. Look up the temporary handshake data
    auth_data = await temp_auth.find_one({"user_id": req.user_id})
    if not auth_data:
        return {"status": "error", "message": "No active request found. Send the code again."}

    client = TelegramClient(StringSession(), API_ID, API_HASH)
    await client.connect()
    try:
        try:
            # 2. EXPLICIT SIGN IN: Using the phone, code, and saved hash
            await client.sign_in(
                phone=auth_data["phone"],
                code=req.code,
                phone_code_hash=auth_data["phone_code_hash"]
            )
        except errors.SessionPasswordNeededError:
            if not req.password:
                return {"status": "2fa_required"}
            await client.sign_in(password=req.password)

        # 3. GENERATE SESSION: If successful, save the permanent StringSession
        string_session = client.session.save()
        await final_sessions.update_one(
            {"user_id": req.user_id},
            {"$set": {
                "session": string_session, 
                "phone": auth_data["phone"],
                "status": "active"
            }},
            upsert=True
        )
        
        # 4. CLEANUP: Delete temp data so the user can re-log later if needed
        await temp_auth.delete_one({"user_id": req.user_id})
        
        return {"status": "success"}

    except errors.PhoneCodeInvalidError:
        return {"status": "error", "message": "Invalid code. Please check and try again."}
    except errors.PhoneCodeExpiredError:
        return {"status": "error", "message": "Code expired. Request a new one."}
    except Exception as e:
        return {"status": "error", "message": str(e)}
    finally:
        await client.disconnect()
