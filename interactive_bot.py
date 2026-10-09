import os
import json
import time
import subprocess
import requests

GEMINI_KEY = os.environ.get("GEMINI_API_KEY")
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
ALLOWED_CHAT_ID = str(os.environ.get("TELEGRAM_CHAT_ID"))
REGION = "us-east-1"

def send_chat_action(action="typing"):
    if not TELEGRAM_BOT_TOKEN or not ALLOWED_CHAT_ID:
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendChatAction"
    try:
        requests.post(url, json={"chat_id": ALLOWED_CHAT_ID, "action": action}, timeout=3)
    except Exception:
        pass

def send_telegram(text: str):
    if not TELEGRAM_BOT_TOKEN or not ALLOWED_CHAT_ID:
        return

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    formatted_text = text.replace("**", "*")

    chunks = []
    current = ""
    for line in formatted_text.split("\n"):
        if len(current) + len(line) + 1 > 3500:
            if current.strip():
                chunks.append(current.strip())
            current = line + "\n"
        else:
            current += line + "\n"
    if current.strip():
        chunks.append(current.strip())

    for chunk in chunks:
        payload = {
            "chat_id": ALLOWED_CHAT_ID,
            "text": chunk,
            "parse_mode": "Markdown",
            "disable_web_page_preview": True
        }
        res = requests.post(url, json=payload, timeout=10)
        if res.status_code != 200:
            payload.pop("parse_mode", None)
            requests.post(url, json=payload, timeout=10)

def discover_active_gemini_models():
    """Dynamically queries Google ModelService to fetch available generateContent models"""
    if not GEMINI_KEY:
        print("[!] GEMINI_API_KEY is not set.")
        return ["gemini-2.5-flash"]

    url = f"https://generativelanguage.googleapis.com/v1beta/models?key={GEMINI_KEY}"
    try:
        res = requests.get(url, timeout=10)
        if res.status_code == 200:
            models = res.json().get("models", [])
            valid_models = []
            for m in models:
                methods = m.get("supportedGenerationMethods", [])
                name = m.get("name", "").replace("models/", "")
                if "generateContent" in methods and "flash" in name:
                    valid_models.append(name)
            if valid_models:
                print(f"[+] Discovered active Gemini Flash models: {valid_models}")
                return valid_models
    except Exception as e:
        print(f"[!] Model discovery error: {e}")

    # Fallback to recommended 2.5 flash model
    return ["gemini-2.5-flash", "gemini-2.5-pro"]

ACTIVE_MODELS = discover_active_gemini_models()

def ask_gemini(prompt: str):
    """Direct Google AI Studio API call targeting active models"""
    if not GEMINI_KEY:
        return "⚠️ Error: `GEMINI_API_KEY` is not set in environment."

    headers = {"Content-Type": "application/json"}
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0.2, "maxOutputTokens": 1500}
    }

    for model in ACTIVE_MODELS:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={GEMINI_KEY}"
        try:
            print(f"[*] Calling Gemini ({model})...")
            res = requests.post(url, headers=headers, json=payload, timeout=15)
            if res.status_code == 200:
                data = res.json()
                return data["candidates"][0]["content"]["parts"][0]["text"]
            else:
                print(f"[!] {model} error ({res.status_code}): {res.text}")
        except Exception as e:
            print(f"[!] Network error on {model}: {e}")
            continue

    return None

def execute_safe_aws_query(command: str):
    clean_cmd = command.strip().replace("`", "").strip()
    parts = clean_cmd.split()

    if len(parts) < 3 or parts[0] != "aws":
        return None

    action = parts[2].lower()
    allowed_verbs = ("describe", "get", "list", "select", "head")

    if not any(action.startswith(v) for v in allowed_verbs):
        return f"Blocked: Action '{action}' is not read-only."

    try:
        print(f"[*] Executing Read Query: {clean_cmd}")
        res = subprocess.run(clean_cmd, shell=True, capture_output=True, text=True, timeout=20)
        if res.returncode == 0:
            output = res.stdout.strip()
            return output[:3000] if output else "Query executed successfully, but returned 0 items."
        else:
            return f"AWS CLI Error: {res.stderr.strip()}"
    except Exception as e:
        return f"Execution Error: {e}"

def handle_user_message(user_text: str):
    send_chat_action("typing")

    # Step 1: Query Planner
    router_prompt = f"""
    You are an AWS infrastructure planner.
    The user is asking: "{user_text}"
    AWS Region: {REGION}

    Instructions:
    - If this question targets live AWS resources (EC2, IAM, S3, VPC, Security Groups, Lambda, RDS, Route53, Subnets, Disks):
      Generate the single best read-only AWS CLI query (using describe, list, or get).
      Output ONLY the exact AWS CLI command. Do NOT use markdown quotes or backticks.
      Example: aws ec2 describe-vpcs --region {REGION} --output json

    - If this question is general advice, troubleshooting, concepts, or greetings:
      Output ONLY: NONE
    """

    decision = ask_gemini(router_prompt)
    aws_data = ""
    executed_cmd = ""

    if decision and "aws " in decision.lower() and "none" not in decision.lower():
        executed_cmd = decision.strip().replace("`", "")
        aws_data = execute_safe_aws_query(executed_cmd)

    send_chat_action("typing")

    # Step 2: English Executive Response Synthesizer
    synthesis_prompt = f"""
    You are an expert Autonomous AWS Cloud Security Watchdog assistant on Telegram.
    User Question: "{user_text}"
    AWS Region: {REGION}
    Executed AWS Query: `{executed_cmd if executed_cmd else 'None (General Query)'}`
    Raw AWS Telemetry:
    {aws_data if aws_data else 'No telemetry query required or empty output.'}

    CRITICAL RULES:
    1. Respond strictly in ENGLISH.
    2. Format cleanly for mobile Telegram using emojis (🖥️, 🔑, 📦, 🛡️, ⚡) and bold headers.
    3. Enclose all resource IDs (`i-xxxx`, `sg-xxxx`, `vpc-xxxx`), ports, role names, and counts in backticks (`value`).
    4. If exposed ports or drift are detected, provide exact copy-paste remediation commands.
    5. Keep answers high-density, professional, and directly actionable.
    """

    final_reply = ask_gemini(synthesis_prompt)

    if final_reply:
        send_telegram(final_reply)
        print("[+] English response delivered to Telegram successfully!")
    else:
        send_telegram("⚠️ AI service is temporarily busy. Please retry in a moment.")

def start_bot():
    active_m = ACTIVE_MODELS[0] if ACTIVE_MODELS else "gemini-2.5-flash"
    print(f"[+] Autonomous AWS Watchdog Agent ({active_m}) is ONLINE...")
    send_telegram(f"🚀 *AWS Cloud Watchdog Active!*\nEngine: `{active_m}`\nLanguage: English\nAsk me anything about your AWS infrastructure, security groups, or general cloud concepts.")

    offset = None
    while True:
        try:
            url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getUpdates"
            params = {"timeout": 30, "offset": offset}
            res = requests.get(url, params=params, timeout=35).json()

            if "result" in res:
                for item in res["result"]:
                    offset = item["update_id"] + 1
                    msg = item.get("message", {})
                    chat_id = str(msg.get("chat", {}).get("id", ""))
                    text = msg.get("text", "")

                    if chat_id == ALLOWED_CHAT_ID and text:
                        print(f"\n[User Message]: {text}")
                        handle_user_message(text)
        except Exception:
            time.sleep(2)

if __name__ == "__main__":
    start_bot()
