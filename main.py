from fastapi import FastAPI, Form
from fastapi.responses import PlainTextResponse
from twilio.twiml.messaging_response import MessagingResponse
from twilio.rest import Client
from sqlalchemy import create_engine, text
from dotenv import load_dotenv
from langchain_perplexity import ChatPerplexity

load_dotenv()
app = FastAPI()
model = ChatPerplexity(model="sonar")
client = Client("Twilio_SID","TwilioAuthToken") 

import time
import hashlib

def generate_otp(secret="my_secret_salt"):
    timestep = int(time.time()) // 30
    data = f"{secret}_{timestep}".encode()
    hash_digest = hashlib.sha256(data).hexdigest()
    otp = str(int(hash_digest, 16))[-6:]
    return otp.zfill(6)


# DB Setup
DB_URL = "DB URL"
engine = create_engine(DB_URL)

user_state = {}  

@app.post("/whatsapp")
async def whatsapp_webhook(Body: str = Form(...), From: str = Form(...)):
    phone = From
    message = Body.strip()
    twilio_response = MessagingResponse()

    state = user_state.get(phone, {}).get("state", "idle")

    with engine.connect() as conn:
        if message.lower() == "/start":
            user_state[phone] = {"state": "awaiting_regid"}
            twilio_response.message("👋 Welcome! Please enter your Registration ID:")
            return PlainTextResponse(str(twilio_response), media_type="application/xml")

        elif state == "awaiting_regid":
            result = conn.execute(text(
                "SELECT registration_id, mobile_number FROM patients WHERE registration_id = :rid"
            ), {"rid": message}).fetchone()

            if result:
                otp = generate_otp()

                # Update OTP in DB
                conn.execute(text(
                    "UPDATE patients SET otp = :otp, verified = FALSE WHERE registration_id = :rid"
                ), {"otp": otp, "rid": message})
                conn.commit()

                # Send OTP to mobile_number from DB
                try:
                    clean_number = result.mobile_number.replace(" ", "").replace("\u200b", "").strip()
                    to_number = f"whatsapp:{clean_number}"

                    print(f"🟡 About to send WhatsApp message to {to_number}")

                    twilio_message = client.messages.create(
                        from_='whatsapp:+14155238886',
                        body=f"Your OTP is {otp}",
                        to=to_number
                    )

                    print("✅ Message sent successfully")
                    # print(f"📨 OTP {otp} sent to {result.mobile_number} via WhatsApp (SID: {twilio_message.sid})")
                    print("✅ Message sent!")
                    print(f"To: {result.mobile_number}")
                    # print(f"SID: {message.sid}")
                    # print(f"Status: {message.status}")


                    print("OTP sent successfully")
                    user_state[phone] = {"state": "awaiting_otp", "regid": message}
                    twilio_response.message("✅ OTP sent to your registered number. Please enter the OTP:")
                except Exception as e:
                    twilio_response.message(f"❌ Failed to send OTP: {str(e)}")
            else:
                twilio_response.message("❌ Invalid Registration ID. Please try again.")

            return PlainTextResponse(str(twilio_response), media_type="application/xml")

        elif state == "awaiting_otp":
            regid = user_state[phone]["regid"]
            result = conn.execute(text(
                "SELECT otp FROM patients WHERE registration_id = :rid"
            ), {"rid": regid}).fetchone()

            if result and result.otp == message:
                conn.execute(text("UPDATE patients SET verified = TRUE WHERE registration_id = :rid"),
                             {"rid": regid})
                conn.commit()
                user_state[phone]["state"] = "verified"

                patient = conn.execute(text("SELECT * FROM patients WHERE registration_id = :rid"),
                                       {"rid": regid}).fetchone()

                reply = (
                    f"🎉 Verified!\n"
                    f"👤 Name: {patient.name}\n"
                    f"📅 Due Date: {patient.due_date}\n"
                    f"💰 Premium: ₹{patient.premium_amount}\n"
                    f"📞 Phone: {patient.mobile_number}\n"
                    f"📌 Status: {patient.status}"
                )
                twilio_response.message(reply)
            else:
                twilio_response.message("❌ Incorrect OTP. Please try again.")

            return PlainTextResponse(str(twilio_response), media_type="application/xml")

        elif state == "verified":
            regid = user_state[phone]["regid"]
            patient = conn.execute(text("SELECT * FROM patients WHERE registration_id = :rid"),
                                   {"rid": regid}).fetchone()

            context = f"""
            Patient Info:
            Name: {patient.name}
            Age: {patient.age}
            Phone: {patient.mobile_number}
            Premium: ₹{patient.premium_amount}
            Due Date: {patient.due_date}
            Last Payment Date: {patient.last_payment_date}
            Status: {patient.status}
            """

            try:
                response = model.invoke(f"{context}\n\nUser asked: {message}, Keep the message concisex short and relevant to the patient information.Do not give unnecessarily long answers.")
                answer = response.content
            except Exception as e:
                answer = f"⚠️ Error: {str(e)}"

            twilio_response.message(answer)
            return PlainTextResponse(str(twilio_response), media_type="application/xml")

        else:
            twilio_response.message("Please type /start to begin.")
            return PlainTextResponse(str(twilio_response), media_type="application/xml")


@app.get("/")
def home():
    return {"status": "Running"}
