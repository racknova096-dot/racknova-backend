from sqlmodel import Session
from auth.routes_auth import User, engine
import hashlib

with Session(engine) as session:
    hashed_pass = hashlib.sha256("12345".encode()).hexdigest()
    user = User(usuario="admin", contrasena=hashed_pass, rol="admin")
    session.add(user)
    session.commit()
    print("✅ Usuario 'admin' creado correctamente con contraseña cifrada")
