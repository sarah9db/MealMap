import sys
import os

# Allow imports from the project root
sys.path.insert(0, os.path.dirname(__file__))

import streamlit as st
import groq

from config import MEAL_PLAN_PROMPT
from agents import meal_agent, shopping_agent
from services import apple_notes, vision


# ── Groq client (cached — created once per session) ──────────────────────────

@st.cache_resource
def get_groq_client():
    return groq.Groq(api_key=st.secrets["GROQ_API_KEY"])


# ── Page config ───────────────────────────────────────────────────────────────

st.set_page_config(page_title="Mise", page_icon="🍳", layout="centered")
st.title("🍳 Mise")
st.caption("From pantry to plate, at the best price possible.")


# ── Sidebar ───────────────────────────────────────────────────────────────────

with st.sidebar:
    st.header("Settings")
    location = st.text_input(
        "Your location",
        placeholder="e.g. 123 Main St, La Jolla CA 92037",
        help="A full address gives the most accurate nearby store distances.",
    )

    st.markdown("---")

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

    if st.button("Clear conversation"):
        st.session_state.meal_messages = []
        st.session_state.shop_messages = []
        st.rerun()


# ── Session state ─────────────────────────────────────────────────────────────

if "meal_messages" not in st.session_state:
    st.session_state.meal_messages = []
if "shop_messages" not in st.session_state:
    st.session_state.shop_messages = []


# ── Shared helpers ────────────────────────────────────────────────────────────

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

tab_meal, tab_shop = st.tabs(["🍳 Meal Planner", "🛒 Grocery Shopper"])


# ── Meal Planner ──────────────────────────────────────────────────────────────

with tab_meal:
    st.caption("Tell me what ingredients you have and I'll plan batch meals for the week.")
    render_chat(st.session_state.meal_messages)

    uploaded_image = st.file_uploader(
        "📎 Attach a photo of your ingredients (optional)",
        type=["jpg", "jpeg", "png", "webp"],
        key="meal_uploader",
    )
    user_input = st.chat_input("List your ingredients...", key="meal_input")

    if (user_input or uploaded_image) and location_guard():
        client = get_groq_client()
        system_prompt = MEAL_PLAN_PROMPT.format(location=location)
        notes_context = st.session_state.get("active_notes", "")

        if uploaded_image:
            # Encode once — reuse for both display and analysis
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


# ── Grocery Shopper ───────────────────────────────────────────────────────────

with tab_shop:
    st.caption("I'll check your nearby stores and find real prices for you.")
    if "active_notes" in st.session_state:
        st.info(f"📝 Notes: {', '.join(st.session_state.active_note_names)}")

    render_chat(st.session_state.shop_messages)

    shop_input = st.chat_input("e.g. 'Find cheapest bulking ingredients near me'", key="shop_input")

    if not st.session_state.shop_messages:
        if st.button("Find cheapest ingredients near me"):
            shop_input = "Find me the cheapest high-calorie bulking ingredients near me."

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
