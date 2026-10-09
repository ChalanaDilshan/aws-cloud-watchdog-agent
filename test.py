import os
from openai import OpenAI

api_key = os.environ.get("OPENROUTER_API_KEY") or os.environ.get("OPENAI_API_KEY")

client = OpenAI(
    base_url="https://openrouter.ai/api/v1",
    api_key=api_key,
)

response = client.chat.completions.create(
    model="nvidia/nemotron-3.5-lightning:free",
    messages=[
        {"role": "user", "content": "How many r's are in the word 'strawberry'?"}
    ],
)

print("\n--- Output ---")
print(response.choices[0].message.content)
