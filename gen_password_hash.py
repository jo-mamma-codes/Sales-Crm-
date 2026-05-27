"""Generate bcrypt password hashes for adding users to st.secrets.

Run from crm/:
    python3 gen_password_hash.py
Then paste output into Streamlit Cloud secrets.
"""
import bcrypt
import getpass

users = []
print("Add users (empty email to stop):")
while True:
    em = input("Email: ").strip()
    if not em:
        break
    nm = input("Display name: ").strip() or em
    pw = getpass.getpass("Password: ")
    h = bcrypt.hashpw(pw.encode(), bcrypt.gensalt()).decode()
    users.append({"email": em, "name": nm, "password_hash": h})
    print(f"  ✓ Added {em}\n")

print("\n--- Paste this into Streamlit Cloud secrets ---\n")
print("[[auth_users]]")
for u in users:
    print(f'email = "{u["email"]}"')
    print(f'name = "{u["name"]}"')
    print(f'password_hash = "{u["password_hash"]}"')
    print("[[auth_users]]")
print("\n(Delete the trailing [[auth_users]])")
