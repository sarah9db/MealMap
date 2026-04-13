import sys
import os
import json
from datetime import datetime

sys.path.insert(0, os.path.dirname(__file__))

import streamlit as st
import groq

from config import MEAL_PLAN_PROMPT
from agents import meal_agent, shopping_agent
from services import apple_notes, vision

HISTORY_FILE = os.path.join(os.path.dirname(__file__), "chat_history.json")


# ── Chat history persistence ──────────────────────────────────────────────────

def load_history() -> dict:
    if os.path.exists(HISTORY_FILE):
        try:
            with open(HISTORY_FILE) as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def save_session(session_id: str, label: str, meal_msgs: list, shop_msgs: list):
    history = load_history()
    history[session_id] = {
        "label": label,
        "saved_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "meal_messages": meal_msgs,
        "shop_messages": shop_msgs,
    }
    with open(HISTORY_FILE, "w") as f:
        json.dump(history, f, indent=2)


def generate_title(client, first_user_message: str) -> str:
    try:
        resp = client.chat.completions.create(
            model="llama-3.1-8b-instant",
            messages=[{
                "role": "user",
                "content": (
                    "Give this chat a short title (4-6 words max, no quotes, no punctuation).\n\n"
                    + first_user_message
                ),
            }],
        )
        return resp.choices[0].message.content.strip()[:50]
    except Exception:
        return first_user_message[:40]


def autosave(client):
    meal = st.session_state.get("meal_messages", [])
    shop = st.session_state.get("shop_messages", [])
    if not meal and not shop:
        return

    if "current_session_id" not in st.session_state:
        st.session_state.current_session_id = datetime.now().strftime("%Y%m%d_%H%M%S")

    if "session_title" not in st.session_state:
        first_msg = (meal or shop)[0].get("content", "")
        if isinstance(first_msg, list):
            first_msg = next((p.get("text", "") for p in first_msg if p.get("type") == "text"), "Chat")
        st.session_state.session_title = generate_title(client, first_msg)

    save_session(
        st.session_state.current_session_id,
        st.session_state.session_title,
        meal,
        shop,
    )


def delete_session(session_id: str):
    history = load_history()
    history.pop(session_id, None)
    with open(HISTORY_FILE, "w") as f:
        json.dump(history, f, indent=2)


# ── Groq client ───────────────────────────────────────────────────────────────

@st.cache_resource
def get_groq_client():
    return groq.Groq(api_key=st.secrets["GROQ_API_KEY"])


# ── Page config ───────────────────────────────────────────────────────────────

st.set_page_config(page_title="Meal Map", page_icon="🗺️", layout="centered")

st.title("🗺️ Meal Map")
st.caption("Plan meals. Find the best prices nearby.")


# ── Sidebar ───────────────────────────────────────────────────────────────────

with st.sidebar:
    st.header("Settings")
    location = st.text_input(
        "Your location",
        placeholder="e.g. 123 Main St, La Jolla CA 92037",
        help="A full address gives the most accurate nearby store distances.",
    )

    st.markdown("---")

    # ── Apple Notes ───────────────────────────────────────────────────────────
    if apple_notes.is_available():
        st.subheader("📝 Apple Notes")
        if st.button("Browse Notes"):
            with st.spinner("Loading notes..."):
                st.session_state.note_titles = apple_notes.get_titles()

        if "note_titles" in st.session_state and st.session_state.note_titles:
            selected = st.multiselect("Select notes to include", st.session_state.note_titles)
            if st.button("Load selected notes") and selected:
                combined = [f"### {t}\n{apple_notes.get_content(t)}" for t in selected]
                st.session_state.active_notes = "Here are my notes:\n\n" + "\n\n".join(combined)
                st.session_state.active_note_names = selected
                st.rerun()
        elif "note_titles" in st.session_state:
            st.info("No notes found.")

        if "active_notes" in st.session_state:
            st.success(f"Active: {', '.join(st.session_state.active_note_names)}")
            if st.button("Clear notes"):
                st.session_state.pop("active_notes", None)
                st.session_state.pop("active_note_names", None)
                st.rerun()

        st.markdown("---")

    # ── Chat history ──────────────────────────────────────────────────────────
    st.subheader("💬 Saved Chats")

    history = load_history()
    if history:
        for sid, session in sorted(history.items(), key=lambda x: x[1]["saved_at"], reverse=True):
            col1, col2 = st.columns([3, 1])
            with col1:
                if st.button(f"{session['label']}\n{session['saved_at']}", key=f"load_{sid}", use_container_width=True):
                    st.session_state.meal_messages = session["meal_messages"]
                    st.session_state.shop_messages = session["shop_messages"]
                    st.session_state.current_session_id = sid
                    st.session_state.session_title = session["label"]
                    st.rerun()
            with col2:
                if st.button("🗑️", key=f"del_{sid}"):
                    delete_session(sid)
                    st.rerun()
    else:
        st.caption("No saved chats yet.")

    st.markdown("---")

    if st.button("New chat", use_container_width=True):
        st.session_state.meal_messages = []
        st.session_state.shop_messages = []
        st.session_state.pop("current_session_id", None)
        st.session_state.pop("session_title", None)
        st.rerun()


# ── Session state ─────────────────────────────────────────────────────────────

if "meal_messages" not in st.session_state:
    st.session_state.meal_messages = []
if "shop_messages" not in st.session_state:
    st.session_state.shop_messages = []


# ── Helpers ───────────────────────────────────────────────────────────────────

def render_chat(messages: list):
    for msg in messages:
        with st.chat_message(msg["role"]):
            if isinstance(msg["content"], list):
                for part in msg["content"]:
                    if part["type"] == "text":
                        st.markdown(part["text"])
                    elif part["type"] == "image_url":
                        st.image(part["image_url"]["url"], width=300)
            else:
                st.markdown(msg["content"])


def handle_response(agent_gen) -> str:
    with st.chat_message("assistant"):
        placeholder = st.empty()
        final_response = ""
        with st.status("Working...", expanded=True) as status:
            for event_type, event_data in agent_gen:
                if event_type == "status":
                    status.update(label=event_data)
                elif event_type == "done":
                    final_response = event_data
                    status.update(label="Done", state="complete", expanded=False)
        placeholder.markdown(final_response)
    return final_response


def location_guard() -> bool:
    if not location:
        st.warning("Enter your location in the sidebar first.")
        return False
    return True


# ── Tabs ──────────────────────────────────────────────────────────────────────

tab_meal, tab_shop = st.tabs(["🍳 Meal Planner", "🛒 Price Finder"])


# ── Meal Planner ──────────────────────────────────────────────────────────────

with tab_meal:
    st.caption("Tell me what ingredients you have and I'll plan batch meals for the week.")
    render_chat(st.session_state.meal_messages)

    uploaded_image = st.file_uploader(
        "Attach a photo of your ingredients (optional)",
        type=["jpg", "jpeg", "png", "webp"],
        key="meal_uploader",
    )
    user_input = st.chat_input("List your ingredients...", key="meal_input")

    if (user_input or uploaded_image) and location_guard():
        client = get_groq_client()
        system_prompt = MEAL_PLAN_PROMPT.format(location=location)
        notes_context = st.session_state.get("active_notes", "")

        if uploaded_image:
            b64, mime = vision.encode_image(uploaded_image)
            data_url = f"data:{mime};base64,{b64}"

            with st.chat_message("user"):
                st.image(data_url, width=300)
                if user_input:
                    st.markdown(user_input)

            st.session_state.meal_messages.append({
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": data_url}},
                    {"type": "text", "text": user_input or "What can I make with these?"},
                ],
            })

            with st.spinner("Analyzing image..."):
                ingredients = vision.analyze_ingredients(client, b64, mime)

            full_message = (
                f"Ingredients from image: {ingredients}\n\n"
                + (user_input or "Create a batch meal prep plan.")
            )
        else:
            with st.chat_message("user"):
                st.markdown(user_input)
            st.session_state.meal_messages.append({"role": "user", "content": user_input})
            full_message = user_input

        if notes_context:
            full_message = f"{notes_context}\n\n{full_message}"

        response = handle_response(
            meal_agent.run(client, st.session_state.meal_messages[:-1], system_prompt, full_message)
        )
        st.session_state.meal_messages.append({"role": "assistant", "content": response})
        autosave(client)


# ── Price Finder ──────────────────────────────────────────────────────────────

with tab_shop:
    st.caption("Find real prices from nearby stores for any ingredient.")
    if "active_notes" in st.session_state:
        st.info(f"📝 Notes active: {', '.join(st.session_state.active_note_names)}")

    render_chat(st.session_state.shop_messages)

    if not st.session_state.shop_messages:
        if st.button("Find cheapest bulking ingredients near me"):
            shop_input = "Find me the cheapest high-calorie bulking ingredients near me."
        else:
            shop_input = None
    else:
        shop_input = None

    typed_input = st.chat_input("e.g. 'tomato paste, rice, eggs'", key="shop_input")
    if typed_input:
        shop_input = typed_input

    if shop_input and location_guard():
        client = get_groq_client()
        notes_context = st.session_state.get("active_notes", "")
        full_message = f"{notes_context}\n\n{shop_input}" if notes_context else shop_input

        with st.chat_message("user"):
            st.markdown(shop_input)
        st.session_state.shop_messages.append({"role": "user", "content": shop_input})

        kroger_id = st.secrets.get("KROGER_CLIENT_ID", "")
        kroger_secret = st.secrets.get("KROGER_CLIENT_SECRET", "")

        response = handle_response(
            shopping_agent.run(client, location, full_message, kroger_id, kroger_secret)
        )
        st.session_state.shop_messages.append({"role": "assistant", "content": response})
        autosave(client)
