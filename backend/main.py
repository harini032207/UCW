import os
import sys
import uuid
import traceback
import shutil
from typing import List, Optional,Dict
from pathlib import Path
from fastapi import FastAPI, Depends, HTTPException, status, Query, Request, File, UploadFile
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from sqlalchemy.orm import Session
from sqlalchemy import or_, and_
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
import json

backend_dir = Path(__file__).resolve().parent
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

import database
import models
import schemas
import auth

# Initialize FastAPI App
app = FastAPI(
    title="Nexus Auth & Social API",
    description="Python FastAPI service with User Search, Profiles, Connection Requests, and Notifications",
    version="1.0.0"
)

# Global Exception Handler to ensure CORS headers are ALWAYS present even on internal errors
@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    print(f"[SERVER_ERROR] Exception on {request.url.path}: {exc}")
    traceback.print_exc()
    origin = request.headers.get("origin", "http://localhost:3000")
    return JSONResponse(
        status_code=500,
        content={"detail": f"SERVER_ERROR: {str(exc)}"},
        headers={
            "Access-Control-Allow-Origin": origin,
            "Access-Control-Allow-Credentials": "true"
        }
    )

# Configure CORS Middleware
origins = [
    "http://localhost:3000",
    "http://localhost:3001",
    "http://127.0.0.1:3000",
    "http://127.0.0.1:3001",
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_origin_regex=r"http://(localhost|127\.0\.0\.1|192\.168\.\d+\.\d+)(:\d+)?",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Static file serving for user uploaded images
uploads_dir = backend_dir / "uploads"
uploads_dir.mkdir(parents=True, exist_ok=True)
app.mount("/uploads", StaticFiles(directory=str(uploads_dir)), name="uploads")

# Startup Event: Ensure PostgreSQL tables exist
@app.on_event("startup")
def startup_event():
    try:
        models.Base.metadata.create_all(bind=database.engine)
        import verify_all_tables
        verify_all_tables.patch_notifications_table()
        print("[SUCCESS] PostgreSQL Database connection verified and tables initialized.")
    except Exception as e:
        print(f"[WARNING] Database initialization exception: {e}")

@app.get("/api/health")
def health_check():
    return {"status": "ok", "service": "Nexus FastAPI Authentication"}

# ----------------------------------------------------
# 1. Google OAuth Token Verification Endpoint
# ----------------------------------------------------
@app.post("/api/auth/google-verify", response_model=schemas.GoogleVerifyResponse)
def google_verify(payload: schemas.GoogleVerifyRequest, db: Session = Depends(database.get_db)):
    if not payload.id_token:
        raise HTTPException(status_code=400, detail="id_token is required")
    
    verified_info = auth.verify_google_id_token(payload.id_token)
    email = verified_info["email"].lower()

    # Check if this email is already registered in PostgreSQL database
    existing_user = db.query(models.User).filter(models.User.email.ilike(email)).first()
    verified_info["exists_in_db"] = True if existing_user else False

    return verified_info

# ----------------------------------------------------
# 2. User Registration Endpoint
# ----------------------------------------------------
@app.post("/api/auth/register", response_model=schemas.AuthTokenResponse)
def register_user(req: schemas.UserRegisterRequest, db: Session = Depends(database.get_db)):
    existing_username = db.query(models.User).filter(models.User.username.ilike(req.username.strip())).first()
    if existing_username:
        raise HTTPException(
            status_code=400,
            detail="USERNAME_ALREADY_TAKEN"
        )
        
    existing_email = db.query(models.User).filter(models.User.email.ilike(req.email.strip())).first()
    if existing_email:
        raise HTTPException(
            status_code=400,
            detail="EMAIL_ALREADY_REGISTERED"
        )

    hashed_pwd = auth.get_password_hash(req.password)
    
    new_user = models.User(
        user_id=uuid.uuid4(),
        username=req.username.strip(),
        display_name=req.display_name.strip() if req.display_name else req.username.strip(),
        email=req.email.strip().lower(),
        phone_number=req.phone.strip() if req.phone else None,
        area=req.area or "SALIGRAMAM_SEC",
        password_hash=hashed_pwd,
        google_id=req.google_token[:50] if req.google_token else None,
        email_verified=True,
        profile_photo=None,
        bio=None,
        skills="Next.js, Python, PostgreSQL, FastAPI",
        interests="Web Development, Cyber Security"
    )
    
    db.add(new_user)
    db.commit()
    db.refresh(new_user)
    
    access_token = auth.create_access_token(data={"sub": new_user.username, "email": new_user.email})
    
    return {
        "access_token": access_token,
        "token_type": "bearer",
        "user": new_user
    }

# ----------------------------------------------------
# 3. User Login Endpoint (Email/Username + Password)
# ----------------------------------------------------
@app.post("/api/auth/login", response_model=schemas.AuthTokenResponse)
def login_user(req: schemas.UserLoginRequest, db: Session = Depends(database.get_db)):
    identifier = req.identifier.strip().lower()
    
    user = db.query(models.User).filter(
        (models.User.username.ilike(identifier)) | (models.User.email.ilike(identifier))
    ).first()
    
    if not user or not auth.verify_password(req.password, user.password_hash):
        raise HTTPException(
            status_code=401,
            detail="INVALID_CREDENTIALS"
        )
        
    access_token = auth.create_access_token(data={"sub": user.username, "email": user.email})
    
    return {
        "access_token": access_token,
        "token_type": "bearer",
        "user": user
    }

# ----------------------------------------------------
# 4. Google Direct Login Endpoint
# ----------------------------------------------------
@app.post("/api/auth/google-login", response_model=schemas.AuthTokenResponse)
def google_login(payload: schemas.GoogleVerifyRequest, db: Session = Depends(database.get_db)):
    if not payload.id_token:
        raise HTTPException(status_code=400, detail="id_token is required")
    
    google_info = auth.verify_google_id_token(payload.id_token)
    email = google_info["email"].lower()

    user = db.query(models.User).filter(models.User.email.ilike(email)).first()
    
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="EMAIL_NOT_REGISTERED"
        )

    access_token = auth.create_access_token(data={"sub": user.username, "email": user.email})
    return {
        "access_token": access_token,
        "token_type": "bearer",
        "user": user
    }

# ----------------------------------------------------
# 5. Get Current Logged In User Endpoint
# ----------------------------------------------------
@app.get("/api/auth/me", response_model=schemas.UserResponse)
def get_me(current_user: models.User = Depends(auth.get_current_user)):
    if not current_user:
        raise HTTPException(status_code=401, detail="NOT_AUTHENTICATED")
    return current_user

# ----------------------------------------------------
# 6. User Search Endpoint (Phase 4)
# ----------------------------------------------------
@app.get("/api/users/search", response_model=List[schemas.UserSearchResult])
def search_users(
    q: str = Query(..., min_length=1),
    db: Session = Depends(database.get_db),
    current_user: Optional[models.User] = Depends(auth.get_current_user)
):
    query_str = f"%{q.strip()}%"
    users = db.query(models.User).filter(
        or_(
            models.User.username.ilike(query_str),
            models.User.display_name.ilike(query_str)
        )
    ).limit(20).all()
    
    # Exclude current logged in user from search results if logged in
    if current_user:
        users = [u for u in users if u.user_id != current_user.user_id]
        
    return users

# ----------------------------------------------------
# 7. User Suggestions Endpoint (Must be before {username} route)
# ----------------------------------------------------
@app.get("/api/users/suggestions", response_model=List[schemas.UserSearchResult])
def get_user_suggestions(
    db: Session = Depends(database.get_db),
    current_user: Optional[models.User] = Depends(auth.get_current_user)
):
    query = db.query(models.User)
    if current_user:
        query = query.filter(models.User.user_id != current_user.user_id)
    return query.limit(5).all()

# ----------------------------------------------------
# 8. User Public Profile Endpoint (Phase 4)
# ----------------------------------------------------
@app.get("/api/users/{username}", response_model=schemas.UserProfileResponse)
def get_user_profile(
    username: str,
    db: Session = Depends(database.get_db),
    current_user: Optional[models.User] = Depends(auth.get_current_user)
):
    target_user = db.query(models.User).filter(models.User.username.ilike(username.strip())).first()
    if not target_user:
        raise HTTPException(status_code=404, detail="USER_NOT_FOUND")

    connection_status = "NOT_CONNECTED"
    connection_id = None

    if current_user:
        if current_user.user_id == target_user.user_id:
            connection_status = "SELF"
        else:
            conn = db.query(models.Connection).filter(
                or_(
                    and_(models.Connection.sender_id == current_user.user_id, models.Connection.receiver_id == target_user.user_id),
                    and_(models.Connection.sender_id == target_user.user_id, models.Connection.receiver_id == current_user.user_id)
                )
            ).first()

            if conn:
                connection_id = conn.connection_id
                if conn.status == "ACCEPTED":
                    connection_status = "ACCEPTED"
                elif conn.status == "PENDING":
                    if conn.sender_id == current_user.user_id:
                        connection_status = "PENDING_SENT"
                    else:
                        connection_status = "PENDING_RECEIVED"
                elif conn.status == "REJECTED":
                    connection_status = "REJECTED"

    return {
        "user_id": target_user.user_id,
        "username": target_user.username,
        "display_name": target_user.display_name,
        "profile_photo": target_user.profile_photo,
        "banner_photo": getattr(target_user, "banner_photo", None),
        "area": target_user.area,
        "bio": target_user.bio,
        "skills": target_user.skills,
        "interests": target_user.interests,
        "connection_status": connection_status,
        "connection_id": connection_id,
        "created_at": target_user.created_at
    }

# ----------------------------------------------------
# 8. User Profile Update Endpoint
# ----------------------------------------------------
@app.put("/api/users/profile", response_model=schemas.UserResponse)
def update_profile(
    payload: schemas.UserProfileUpdate,
    db: Session = Depends(database.get_db),
    current_user: models.User = Depends(auth.get_current_user)
):
    if not current_user:
        raise HTTPException(status_code=401, detail="NOT_AUTHENTICATED")

    if payload.display_name is not None:
        current_user.display_name = payload.display_name.strip()
    if payload.bio is not None:
        current_user.bio = payload.bio.strip()
    if payload.area is not None:
        current_user.area = payload.area.strip()
    if payload.phone_number is not None:
        current_user.phone_number = payload.phone_number.strip()
    if payload.skills is not None:
        current_user.skills = payload.skills.strip()
    if payload.interests is not None:
        current_user.interests = payload.interests.strip()
    if payload.profile_photo is not None:
        current_user.profile_photo = payload.profile_photo
    if payload.banner_photo is not None:
        current_user.banner_photo = payload.banner_photo

    db.commit()
    db.refresh(current_user)
    return current_user

# ----------------------------------------------------
# 9. Image Upload Endpoint
# ----------------------------------------------------
@app.post("/api/upload/image")
async def upload_image(file: UploadFile = File(...)):
    if not file.content_type.startswith("image/"):
        raise HTTPException(status_code=400, detail="INVALID_FILE_TYPE: File must be an image")
    
    file_ext = Path(file.filename).suffix or ".jpg"
    unique_filename = f"{uuid.uuid4()}{file_ext}"
    file_path = uploads_dir / unique_filename
    
    with open(file_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)
        
    return {"url": f"/uploads/{unique_filename}"}

# ----------------------------------------------------
# 10. Connection Request Endpoint (Phase 5)
# ----------------------------------------------------
@app.post("/api/connections/request", response_model=schemas.ConnectionResponse)
def send_connection_request(
    payload: schemas.ConnectionRequestPayload,
    db: Session = Depends(database.get_db),
    current_user: models.User = Depends(auth.get_current_user)
):
    if not current_user:
        raise HTTPException(status_code=401, detail="NOT_AUTHENTICATED")

    if payload.receiver_id == current_user.user_id:
        raise HTTPException(status_code=400, detail="CANNOT_CONNECT_WITH_SELF")

    target_user = db.query(models.User).filter(models.User.user_id == payload.receiver_id).first()
    if not target_user:
        raise HTTPException(status_code=404, detail="RECEIVER_NOT_FOUND")

    existing_conn = db.query(models.Connection).filter(
        or_(
            and_(models.Connection.sender_id == current_user.user_id, models.Connection.receiver_id == payload.receiver_id),
            and_(models.Connection.sender_id == payload.receiver_id, models.Connection.receiver_id == current_user.user_id)
        )
    ).first()

    if existing_conn:
        if existing_conn.status == "ACCEPTED":
            raise HTTPException(status_code=400, detail="ALREADY_CONNECTED")
        elif existing_conn.status == "PENDING":
            raise HTTPException(status_code=400, detail="CONNECTION_REQUEST_ALREADY_PENDING")
        else:
            # Re-send if previously rejected
            existing_conn.sender_id = current_user.user_id
            existing_conn.receiver_id = payload.receiver_id
            existing_conn.status = "PENDING"
            conn = existing_conn
    else:
        conn = models.Connection(
            connection_id=uuid.uuid4(),
            sender_id=current_user.user_id,
            receiver_id=payload.receiver_id,
            status="PENDING"
        )
        db.add(conn)

    # Create Notification for Receiver
    notif = models.Notification(
        notification_id=uuid.uuid4(),
        user_id=payload.receiver_id,
        sender_id=current_user.user_id,
        type="CONNECTION_REQUEST",
        message=f"{current_user.display_name or current_user.username} sent you a connection request."
    )
    db.add(notif)

    db.commit()
    db.refresh(conn)

    return {
        "connection_id": conn.connection_id,
        "sender_id": conn.sender_id,
        "receiver_id": conn.receiver_id,
        "status": conn.status,
        "created_at": conn.created_at,
        "other_user_id": target_user.user_id,
        "other_username": target_user.username,
        "other_display_name": target_user.display_name,
        "other_profile_photo": target_user.profile_photo
    }

# ----------------------------------------------------
# 11. Accept Connection Endpoint (Phase 5)
# ----------------------------------------------------
@app.post("/api/connections/accept", response_model=schemas.ConnectionResponse)
def accept_connection(
    payload: schemas.ConnectionActionPayload,
    db: Session = Depends(database.get_db),
    current_user: models.User = Depends(auth.get_current_user)
):
    if not current_user:
        raise HTTPException(status_code=401, detail="NOT_AUTHENTICATED")

    conn = db.query(models.Connection).filter(models.Connection.connection_id == payload.connection_id).first()
    if not conn:
        raise HTTPException(status_code=404, detail="CONNECTION_NOT_FOUND")

    if conn.receiver_id != current_user.user_id and conn.sender_id != current_user.user_id:
        raise HTTPException(status_code=403, detail="NOT_AUTHORIZED")

    conn.status = "ACCEPTED"

    # Create Notification for Original Sender
    other_user_id = conn.sender_id if conn.receiver_id == current_user.user_id else conn.receiver_id
    other_user = db.query(models.User).filter(models.User.user_id == other_user_id).first()

    notif = models.Notification(
        notification_id=uuid.uuid4(),
        user_id=other_user_id,
        sender_id=current_user.user_id,
        type="CONNECTION_ACCEPTED",
        message=f"{current_user.display_name or current_user.username} accepted your connection request."
    )
    db.add(notif)

    db.commit()
    db.refresh(conn)

    return {
        "connection_id": conn.connection_id,
        "sender_id": conn.sender_id,
        "receiver_id": conn.receiver_id,
        "status": conn.status,
        "created_at": conn.created_at,
        "other_user_id": other_user.user_id,
        "other_username": other_user.username,
        "other_display_name": other_user.display_name,
        "other_profile_photo": other_user.profile_photo
    }

# ----------------------------------------------------
# 12. Reject Connection Endpoint (Phase 5)
# ----------------------------------------------------
@app.post("/api/connections/reject")
def reject_connection(
    payload: schemas.ConnectionActionPayload,
    db: Session = Depends(database.get_db),
    current_user: models.User = Depends(auth.get_current_user)
):
    if not current_user:
        raise HTTPException(status_code=401, detail="NOT_AUTHENTICATED")

    conn = db.query(models.Connection).filter(models.Connection.connection_id == payload.connection_id).first()
    if not conn:
        raise HTTPException(status_code=404, detail="CONNECTION_NOT_FOUND")

    conn.status = "REJECTED"
    db.commit()
    return {"status": "REJECTED", "connection_id": conn.connection_id}

# ----------------------------------------------------
# 13. Pending Connection Requests List Endpoint
# ----------------------------------------------------
@app.get("/api/connections/pending", response_model=List[schemas.ConnectionResponse])
def get_pending_connections(
    db: Session = Depends(database.get_db),
    current_user: models.User = Depends(auth.get_current_user)
):
    if not current_user:
        raise HTTPException(status_code=401, detail="NOT_AUTHENTICATED")

    pending_conns = db.query(models.Connection).filter(
        and_(models.Connection.receiver_id == current_user.user_id, models.Connection.status == "PENDING")
    ).all()

    result = []
    for conn in pending_conns:
        sender = db.query(models.User).filter(models.User.user_id == conn.sender_id).first()
        if sender:
            result.append({
                "connection_id": conn.connection_id,
                "sender_id": conn.sender_id,
                "receiver_id": conn.receiver_id,
                "status": conn.status,
                "created_at": conn.created_at,
                "other_user_id": sender.user_id,
                "other_username": sender.username,
                "other_display_name": sender.display_name,
                "other_profile_photo": sender.profile_photo
            })
    return result

# ----------------------------------------------------
# 14. Accepted Connections List Endpoint
# ----------------------------------------------------
@app.get("/api/connections/list", response_model=List[schemas.ConnectionResponse])
def get_accepted_connections(
    db: Session = Depends(database.get_db),
    current_user: models.User = Depends(auth.get_current_user)
):
    if not current_user:
        raise HTTPException(status_code=401, detail="NOT_AUTHENTICATED")

    accepted_conns = db.query(models.Connection).filter(
        and_(
            or_(models.Connection.sender_id == current_user.user_id, models.Connection.receiver_id == current_user.user_id),
            models.Connection.status == "ACCEPTED"
        )
    ).all()

    result = []
    for conn in accepted_conns:
        other_id = conn.receiver_id if conn.sender_id == current_user.user_id else conn.sender_id
        other_user = db.query(models.User).filter(models.User.user_id == other_id).first()
        if other_user:
            result.append({
                "connection_id": conn.connection_id,
                "sender_id": conn.sender_id,
                "receiver_id": conn.receiver_id,
                "status": conn.status,
                "created_at": conn.created_at,
                "other_user_id": other_user.user_id,
                "other_username": other_user.username,
                "other_display_name": other_user.display_name,
                "other_profile_photo": other_user.profile_photo
            })
    return result

# ----------------------------------------------------
# 15. Notifications List Endpoint
# ----------------------------------------------------
@app.get("/api/notifications", response_model=List[schemas.NotificationResponse])
def get_notifications(
    db: Session = Depends(database.get_db),
    current_user: models.User = Depends(auth.get_current_user)
):
    if not current_user:
        raise HTTPException(status_code=401, detail="NOT_AUTHENTICATED")

    notifs = db.query(models.Notification).filter(
        models.Notification.user_id == current_user.user_id
    ).order_by(models.Notification.created_at.desc()).limit(20).all()

    result = []
    for n in notifs:
        sender = db.query(models.User).filter(models.User.user_id == n.sender_id).first()
        result.append({
            "notification_id": n.notification_id,
            "user_id": n.user_id,
            "sender_id": n.sender_id,
            "sender_username": sender.username if sender else "unknown",
            "sender_display_name": sender.display_name if sender else None,
            "sender_profile_photo": sender.profile_photo if sender else None,
            "type": n.type,
            "message": n.message,
            "is_read": n.is_read,
            "created_at": n.created_at
        })
    return result

# ----------------------------------------------------
# 16. Mark Notifications as Read Endpoint
# ----------------------------------------------------
@app.put("/api/notifications/read")
def mark_notifications_read(
    db: Session = Depends(database.get_db),
    current_user: models.User = Depends(auth.get_current_user)
):
    if not current_user:
        raise HTTPException(status_code=401, detail="NOT_AUTHENTICATED")

    db.query(models.Notification).filter(
        and_(models.Notification.user_id == current_user.user_id, models.Notification.is_read == False)
    ).update({"is_read": True})
    db.commit()

    return {"status": "success"}
class ConnectionManager:
    def __init__(self):
        # Store dict: { "user_id": websocket_instance }
        self.active_connections: Dict[str, WebSocket] = {}

    async def connect(self, user_id: str, websocket: WebSocket):
        await websocket.accept()
        self.active_connections[str(user_id)] = websocket
        print(f"✅ USER CONNECTED: {user_id}")

    def disconnect(self, user_id: str):
        if str(user_id) in self.active_connections:
            del self.active_connections[str(user_id)]
            print(f"🔌 USER DISCONNECTED: {user_id}")

manager = ConnectionManager()

@app.websocket("/ws/chat/{user_id}")
async def websocket_endpoint(websocket: WebSocket, user_id: str):
    await manager.connect(str(user_id), websocket)
    try:
        while True:
            raw_data = await websocket.receive_text()
            print(f"📩 RECEIVED FROM {user_id}: {raw_data}")
            
            try:
                # Parse incoming string to JSON
                data = json.loads(raw_data)
                receiver_id = str(data.get("receiver_id"))
                
                # 1. Receiver-ku message anuppu (Online-la irundha)
                if receiver_id in manager.active_connections:
                    receiver_ws = manager.active_connections[receiver_id]
                    await receiver_ws.send_text(raw_data)
                    print(f"🚀 DELIVERED TO RECEIVER: {receiver_id}")
                else:
                    print(f"⚠️ RECEIVER {receiver_id} IS OFFLINE (Saved to DB only)")

            except Exception as e:
                print(f"❌ Error processing message: {e}")

    except WebSocketDisconnect:
        manager.disconnect(str(user_id))