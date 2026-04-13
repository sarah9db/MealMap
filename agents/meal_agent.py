from config import TEXT_MODEL


def run(client, chat_history: list, system_prompt: str, user_message: str):
    """Single-turn LLM call for meal planning."""
    messages = [{"role": "system", "content": system_prompt}]
    for msg in chat_history:
        if isinstance(msg["content"], list):
            text = " ".join(p.get("text", "") for p in msg["content"] if p.get("type") == "text")
            messages.append({"role": msg["role"], "content": text})
        else:
            messages.append({"role": msg["role"], "content": msg["content"]})
    messages.append({"role": "user", "content": user_message})

    yield ("status", "Planning your meals...")
    resp = client.chat.completions.create(model=TEXT_MODEL, messages=messages)
    yield ("done", resp.choices[0].message.content)
