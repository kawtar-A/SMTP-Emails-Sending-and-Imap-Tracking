# -*- coding: utf-8 -*-
"""Streamlit APP - Newsletter Sender"""

import streamlit as st
import pandas as pd
import uuid
from pathlib import Path
from datetime import datetime
import hashlib, re, time, os, smtplib
from pytz import timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formataddr, make_msgid
from email.header import Header

import gspread
from google.oauth2.service_account import Credentials

# === CONFIG ===
TEMPLATE_PATH = Path("newsletter_template.html")
SENDER_EMAIL = st.secrets["SENDER_EMAIL"]
APP_PASSWORD = st.secrets["APP_PASSWORD"]
SPREADSHEET_ID = st.secrets["SPREADSHEET_ID"]
GAS_TRACKING_URL = st.secrets["GAS_TRACKING_URL"]
SMTP_SERVER, SMTP_PORT = "smtp.gmail.com", 465
SENDER_NAME = "Victoria Branson"
BCC_EMAIL = "8150892@bcc.hubspot.com"

# === Google Sheets Auth ===
def get_gsheet_client():
    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive"
    ]
    creds = Credentials.from_service_account_info(
        st.secrets["google_service_account"], scopes=scopes
    )
    return gspread.authorize(creds)

def load_articles(vertical=None):
    """Load articles. If vertical is provided, filter by that vertical."""
    client = get_gsheet_client()
    sheet = client.open_by_key(SPREADSHEET_ID)
    ws = sheet.worksheet("Newsletter Articles-Week1")
    records = ws.get_all_records()

    if vertical:
        records = [r for r in records if r.get("Vertical", "").lower() == vertical.lower()]

    # Expecting columns: Title, Subtitle, Img, Url
    articles = []
    for r in records[:5]:
        articles.append({
            "title": r.get("Title", ""),
            "subtitle": r.get("Subtitle", ""),
            "img": r.get("Img", "https://via.placeholder.com/120"),
            "url": r.get("Url", "#")
        })
    return articles

# === HELPERS ===
def email_hash(addr): return hashlib.sha256(addr.lower().encode()).hexdigest()[:16]
def truncate(s, n=140): return (s or "").strip()[:max(0, n)].rstrip("…") + ("…" if len(s) > n else "")
def strip_html_to_text(h): return re.sub(r"<.*?>", "", h).strip()
def build_open_pixel(b, s, nid, sid, mid, e):
    return f'<img src="{GAS_TRACKING_URL}?t=open&b={b}&s={s}&nid={nid}&sid={sid}&mid={mid}&e={e}" width="1" height="1" style="display:none;">'
def build_tracking_link(t, b, s, nid, sid, mid, e, to=""):
    return f"{GAS_TRACKING_URL}?t={t}&b={b}&s={s}&nid={nid}&sid={sid}&mid={mid}&e={e}&to={to}"

# === STREAMLIT UI ===
st.title("📬 Newsletter Sender App")
st.markdown("Upload contact CSV, pick a vertical, preview newsletter(s), then send.")

with st.sidebar:
    vertical_input = st.text_input("Vertical (leave empty to preview ALL)")
    batch_name = st.text_input("Batch Name", value="Newsletter_Batch")
    step = st.number_input("Sequence Step", min_value=1, value=1)

contact_file = st.file_uploader("Upload contacts CSV", type="csv")

# === Load Template ===
if not TEMPLATE_PATH.exists():
    st.error("Template file not found.")
    st.stop()
html_template = TEMPLATE_PATH.read_text(encoding="utf-8")

# === Actions ===
if contact_file is not None:
    df = pd.read_csv(contact_file)
    st.success(f"Loaded {len(df)} contacts")

    preview_btn = st.button("🔍 Preview Newsletter(s)")
    send_btn = st.button("✉️ Send Emails")

    # --- PREVIEW ---
    if preview_btn:
        if vertical_input:
            verticals = [vertical_input]
        else:
            # derive all unique verticals in GSheet
            client = get_gsheet_client()
            ws = client.open_by_key(SPREADSHEET_ID).worksheet("Newsletter Articles-Week1")
            all_recs = ws.get_all_records()
            verticals = sorted(set(r.get("Vertical", "General") for r in all_recs))

        for vert in verticals:
            articles = load_articles(vert)
            preview_html = html_template.replace("{{vertical_name}}", vert.title())
            for i, art in enumerate(articles, 1):
                preview_html = preview_html.replace(f"{{{{news{i}_title}}}}", art["title"])
                preview_html = preview_html.replace(f"{{{{news{i}_subtitle}}}}", truncate(art["subtitle"]))
                preview_html = preview_html.replace(f"{{{{news{i}_img}}}}", art["img"])
                preview_html = preview_html.replace(f"{{{{news{i}_url}}}}", art["url"])
            preview_html = preview_html.replace("{{prefs_link}}", "#")
            preview_html = preview_html.replace("{{unsub_link}}", "#")

            st.markdown(f"### Preview – {vert}")
            st.components.v1.html(preview_html, height=800, scrolling=True)

    # --- SEND ---
    if send_btn:
        if not vertical_input:
            st.error("❌ Please enter a vertical before sending.")
        else:
            articles = load_articles(vertical_input)
            server = smtplib.SMTP_SSL(SMTP_SERVER, SMTP_PORT)
            server.login(SENDER_EMAIL, APP_PASSWORD)
            with server:
                for i, row in df.iterrows():
                    email = row.get("Email")
                    if not email or "@" not in email:
                        continue

                    mid = make_msgid()
                    ehash = email_hash(email)

                    # Build personalized HTML
                    html = html_template.replace("{{vertical_name}}", vertical_input.title())
                    for j, art in enumerate(articles, 1):
                        html = html.replace(f"{{{{news{j}_title}}}}", art["title"])
                        html = html.replace(f"{{{{news{j}_subtitle}}}}", truncate(art["subtitle"]))
                        html = html.replace(f"{{{{news{j}_img}}}}", art["img"])
                        html = html.replace(f"{{{{news{j}_url}}}}", art["url"])
                    html = html.replace("{{prefs_link}}", build_tracking_link("prefs", batch_name, step, "nid", ehash, mid.strip("<>"), ehash))
                    html = html.replace("{{unsub_link}}", build_tracking_link("unsubscribe", batch_name, step, "nid", ehash, mid.strip("<>"), ehash))
                    html = html.replace("</body>", f"{build_open_pixel(batch_name, step, 'nid', ehash, mid.strip('<>'), ehash)}</body>")

                    plain = strip_html_to_text(html)
                    subject = f"Top 5 {vertical_input} Stories"

                    msg = MIMEMultipart("alternative")
                    msg["From"] = formataddr((SENDER_NAME, SENDER_EMAIL))
                    msg["To"] = email
                    msg["Bcc"] = BCC_EMAIL
                    msg["Subject"] = Header(subject, "utf-8")
                    msg["Message-Id"] = mid
                    msg.attach(MIMEText(plain, "plain", "utf-8"))
                    msg.attach(MIMEText(html, "html", "utf-8"))

                    try:
                        server.sendmail(SENDER_EMAIL, [email, BCC_EMAIL], msg.as_bytes())
                        st.success(f"✅ Sent: {email}")
                    except Exception as e:
                        st.error(f"❌ Failed: {email} → {e}")

                    time.sleep(0.3)
