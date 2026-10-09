import os
import json
import boto3
import requests
from openai import OpenAI

# 1. Credentials
OPENROUTER_KEY = os.environ.get("OPENROUTER_API_KEY") or os.environ.get("OPENAI_API_KEY")
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

client = OpenAI(
    base_url="https://openrouter.ai/api/v1",
    api_key=OPENROUTER_KEY,
)

REGION = "us-east-1"
ec2 = boto3.client("ec2", region_name=REGION)

FREE_MODELS = [
    "nvidia/nemotron-3.5-lightning:free",
    "meta-llama/llama-3.1-8b-instruct:free",
    "google/gemini-2.0-flash-exp:free"
]

def send_telegram_alert(message: str):
    """පේළි මැදින් නොකැඩෙන සේ Telegram Markdown හරහා පණිවිඩ යැවීම"""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("[!] Warning: Telegram credentials missing.")
        return

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"

    # Line-by-line smart chunking (Max 3500 chars to prevent truncation)
    max_length = 3500
    chunks = []
    current_chunk = ""

    for line in message.split("\n"):
        if len(current_chunk) + len(line) + 1 > max_length:
            if current_chunk.strip():
                chunks.append(current_chunk.strip())
            current_chunk = line + "\n"
        else:
            current_chunk += line + "\n"

    if current_chunk.strip():
        chunks.append(current_chunk.strip())

    for idx, chunk in enumerate(chunks):
        # 1. පළමුව Markdown styling සමඟ උත්සාහ කිරීම
        payload = {
            "chat_id": TELEGRAM_CHAT_ID,
            "text": chunk,
            "parse_mode": "Markdown",
            "disable_web_page_preview": True
        }
        try:
            res = requests.post(url, json=payload, timeout=15)
            # Markdown parse error එකක් ආවොත් Plain Text ලෙස යැවීම
            if res.status_code != 200:
                payload.pop("parse_mode", None)
                res = requests.post(url, json=payload, timeout=15)

            if res.status_code == 200:
                print(f"[+] Alert part {idx+1}/{len(chunks)} sent successfully!")
            else:
                print(f"[!] Telegram API error: {res.text}")
        except Exception as e:
            print(f"[!] Telegram connection error: {e}")

def scan_security_groups():
    print("[+] Scanning Security Groups...")
    response = ec2.describe_security_groups()
    risks = []
    
    for sg in response.get("SecurityGroups", []):
        for perm in sg.get("IpPermissions", []):
            for ip_range in perm.get("IpRanges", []):
                if ip_range.get("CidrIp") == "0.0.0.0/0":
                    from_port = perm.get("FromPort", "ALL")
                    to_port = perm.get("ToPort", "ALL")
                    risks.append({
                        "Type": "SecurityGroupDrift",
                        "GroupId": sg["GroupId"],
                        "GroupName": sg["GroupName"],
                        "Protocol": perm.get("IpProtocol", "ALL"),
                        "OpenPorts": f"{from_port}-{to_port}",
                        "Source": "0.0.0.0/0"
                    })
    return risks

def scan_orphaned_volumes():
    print("[+] Scanning Unattached EBS Volumes...")
    response = ec2.describe_volumes(
        Filters=[{'Name': 'status', 'Values': ['available']}]
    )
    wasted_disks = []
    for vol in response.get("Volumes", []):
        wasted_disks.append({
            "Type": "OrphanedDiskWaste",
            "VolumeId": vol["VolumeId"],
            "SizeGiB": vol["Size"],
            "VolumeType": vol["VolumeType"]
        })
    return wasted_disks

def run_watchdog():
    sg_findings = scan_security_groups()
    disk_findings = scan_orphaned_volumes()
    
    total_findings = sg_findings + disk_findings
    
    if not total_findings:
        msg = (
            f"🛡️ *AWS CLOUD BASELINE CLEAN*\n"
            f"Region: `{REGION}`\n\n"
            f"✅ Security Groups: No unrestricted ports found\n"
            f"✅ EBS Volumes: No unattached orphaned disks found"
        )
        print(msg)
        send_telegram_alert(msg)
        return

    # Mobile Telegram එකට විශේෂයෙන් සකස් කළ Prompt එක
    prompt = f"""
    You are a Senior Cloud Security & FinOps Automation Watchdog.
    AWS Region: {REGION}

    Telemetries:
    {json.dumps(total_findings, indent=2)}

    Format an executive audit report optimized specifically for a TELEGRAM MOBILE CHAT:
    - DO NOT use markdown tables (tables break on mobile screens).
    - Use clean bullet points, emojis (🛡️, 🚨, 💰, ⚡), and bold headers.
    - Keep descriptions compact and high-density.

    Follow this structure:
    1. 📊 *EXECUTIVE SUMMARY* (Total drift count, critical risk rating)
    2. 🚨 *CRITICAL SECURITY DRIFTS* (Group name, ID, exposed ports)
    3. 💰 *FINOPS & DISK DRIFT* (Orphaned EBS volumes or 'None detected')
    4. ⚡ *QUICK REMEDIATION COMMANDS* (Provide clean bash code blocks with exact copy-paste aws ec2 commands)
    """

    full_report = ""

    for model_name in FREE_MODELS:
        try:
            print(f"[*] Analyzing with {model_name}...")
            response = client.chat.completions.create(
                model=model_name,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.2
            )
            full_report = response.choices[0].message.content
            if full_report.strip():
                break
        except Exception as err:
            print(f"[!] Model {model_name} failed ({err}). Trying fallback...")
            continue

    if full_report.strip():
        header = f"🚨 *AWS DRIFT WATCHDOG AUDIT* | `{REGION}`\n━━━━━━━━━━━━━━━━━━━━\n\n"
        final_alert = header + full_report
        print("\n[*] Sending formatted alert to Telegram...")
        send_telegram_alert(final_alert)
    else:
        print("[!] Failed to generate report with all models.")

if __name__ == "__main__":
    run_watchdog()
