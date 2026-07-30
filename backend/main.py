import os
import sys
import uuid
import traceback
import shutil
import datetime
from typing import List, Optional, Dict
from pathlib import Path
from fastapi import FastAPI, Depends, HTTPException, status, Query, Request, File, UploadFile, WebSocket, WebSocketDisconnect, Form
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from sqlalchemy.orm import Session
from sqlalchemy import or_, and_
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
    description="Python FastAPI service with User Search, Profiles, Connection Requests, Notifications, Chat Persistence, and Post Feeds",
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
# 6. User Search Endpoint
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
# 7. User Suggestions Endpoint
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
# 8. User Public Profile Endpoint
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
# 9. User Profile Update Endpoint
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
# 10. Image Upload Endpoint
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
# 11. Connection Request Endpoint
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
# 12. Accept Connection Endpoint
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
# 13. Reject Connection Endpoint
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
# 14. Pending Connection Requests List Endpoint
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
# 15. Accepted Connections List Endpoint
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
# 16. Notifications List Endpoint
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
# 17. Mark Notifications as Read Endpoint
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

# ----------------------------------------------------
# 18. FETCH HISTORICAL CHAT MESSAGES
# ----------------------------------------------------
@app.get("/api/chat/history/{other_user_id}")
def get_chat_history(
    other_user_id: str,
    db: Session = Depends(database.get_db),
    current_user: models.User = Depends(auth.get_current_user)
):
    if not current_user:
        raise HTTPException(status_code=401, detail="NOT_AUTHENTICATED")

    c_uid = current_user.user_id
    o_uid = uuid.UUID(other_user_id)

    # Mark incoming messages as read when chat room is opened
    db.query(models.Message).filter(
        models.Message.sender_id == o_uid,
        models.Message.receiver_id == c_uid,
        models.Message.is_read == False
    ).update({"is_read": True})
    db.commit()

    # Query all messages exchanged between current user and other user
    messages = db.query(models.Message).filter(
        or_(
            and_(models.Message.sender_id == c_uid, models.Message.receiver_id == o_uid),
            and_(models.Message.sender_id == o_uid, models.Message.receiver_id == c_uid)
        )
    ).order_by(models.Message.created_at.asc()).all()

    return [
        {
            "message_id": str(msg.message_id),
            "sender_id": str(msg.sender_id),
            "receiver_id": str(msg.receiver_id),
            "content": msg.content,
            "is_read": msg.is_read,
            "created_at": msg.created_at.isoformat()
        }
        for msg in messages
    ]

# ----------------------------------------------------
# 19. WEBSOCKET REALTIME CHAT & PERSISTENCE
# ----------------------------------------------------
class ConnectionManager:
    def __init__(self):
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
    db = database.SessionLocal()
    
    try:
        while True:
            raw_data = await websocket.receive_text()
            print(f"📩 RECEIVED FROM {user_id}: {raw_data}")
            
            try:
                data = json.loads(raw_data)
                sender_id_str = str(data.get("sender_id", user_id))
                receiver_id_str = str(data.get("receiver_id"))
                content = data.get("content")

                if sender_id_str and receiver_id_str and content:
                    s_uuid = uuid.UUID(sender_id_str)
                    r_uuid = uuid.UUID(receiver_id_str)

                    # 1. Persistent Save: Add to Message Table
                    new_msg = models.Message(
                        message_id=uuid.uuid4(),
                        sender_id=s_uuid,
                        receiver_id=r_uuid,
                        content=content,
                        is_read=False
                    )
                    db.add(new_msg)

                    # 2. Notification Table: Entry for receiver
                    sender_user = db.query(models.User).filter(models.User.user_id == s_uuid).first()
                    sender_name = sender_user.display_name or sender_user.username if sender_user else "Someone"

                    notif = models.Notification(
                        notification_id=uuid.uuid4(),
                        user_id=r_uuid,
                        sender_id=s_uuid,
                        type="NEW_MESSAGE",
                        message=f"{sender_name}: {content[:30]}...",
                        is_read=False
                    )
                    db.add(notif)
                    db.commit()
                    db.refresh(new_msg)

                    # 3. Build enriched response payload
                    payload = {
                        "event_type": "CHAT_MESSAGE",
                        "message_id": str(new_msg.message_id),
                        "sender_id": str(new_msg.sender_id),
                        "receiver_id": str(new_msg.receiver_id),
                        "content": new_msg.content,
                        "is_read": new_msg.is_read,
                        "created_at": new_msg.created_at.isoformat()
                    }

                    # 4. Deliver live to Receiver via WebSocket if Online
                    if receiver_id_str in manager.active_connections:
                        receiver_ws = manager.active_connections[receiver_id_str]
                        await receiver_ws.send_text(json.dumps(payload))
                        print(f"🚀 DELIVERED TO RECEIVER: {receiver_id_str}")
                    else:
                        print(f"⚠️ RECEIVER {receiver_id_str} IS OFFLINE (Saved to DB)")

            except Exception as e:
                print(f"❌ Error processing message logic: {e}")
                db.rollback()

    except WebSocketDisconnect:
        manager.disconnect(str(user_id))
    finally:
        db.close()


# ----------------------------------------------------
# 20. POST FEED & LIKE SYSTEM ENDPOINTS
# ----------------------------------------------------
@app.post("/api/posts/create")
async def create_post(
    content: Optional[str] = Form(None),
    file: Optional[UploadFile] = File(None),
    db: Session = Depends(database.get_db),
    current_user: models.User = Depends(auth.get_current_user)
):
    if not current_user:
        raise HTTPException(status_code=401, detail="NOT_AUTHENTICATED")

    if not content and not file:
        raise HTTPException(status_code=400, detail="POST_CANNOT_BE_EMPTY")

    image_url = None
    if file:
        if not file.content_type.startswith("image/"):
            raise HTTPException(status_code=400, detail="INVALID_FILE_TYPE")
        file_ext = Path(file.filename).suffix or ".jpg"
        unique_filename = f"{uuid.uuid4()}{file_ext}"
        file_path = uploads_dir / unique_filename
        
        with open(file_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)
        image_url = f"/uploads/{unique_filename}"

    new_post = models.Post(
        id=str(uuid.uuid4()),
        user_id=current_user.user_id,
        content=content.strip() if content else "",
        image_url=image_url,
        created_at=datetime.datetime.utcnow()
    )
    db.add(new_post)
    db.commit()
    db.refresh(new_post)

    return {
        "id": str(new_post.id),
        "author_id": str(current_user.user_id),
        "author_name": current_user.display_name or current_user.username,
        "author_username": current_user.username,
        "author_photo": current_user.profile_photo,
        "content": new_post.content,
        "image_url": new_post.image_url,
        "likes_count": 0,
        "comments_count": 0,
        "is_liked": False,
        "created_at": new_post.created_at.isoformat()
    }


@app.get("/api/posts/feed")
def get_feed(
    db: Session = Depends(database.get_db),
    current_user: Optional[models.User] = Depends(auth.get_current_user)
):
    posts = db.query(models.Post).order_by(models.Post.created_at.desc()).limit(50).all()
    feed_data = []

    for post in posts:
        author = db.query(models.User).filter(models.User.user_id == post.user_id).first()
        likes_count = getattr(post, "likes_count", 0) or 0
        comments_count = getattr(post, "comments_count", 0) or 0
        
        is_liked = False
        if current_user and hasattr(models, "PostLike"):
            like_exists = db.query(models.PostLike).filter(
                and_(models.PostLike.post_id == post.id, models.PostLike.user_id == current_user.user_id)
            ).first()
            is_liked = bool(like_exists)

        feed_data.append({
            "id": str(post.id),
            "author_id": str(author.user_id) if author else None,
            "author_name": author.display_name or author.username if author else "Unknown",
            "author_username": author.username if author else "unknown",
            "author_photo": author.profile_photo if author else None,
            "content": post.content,
            "image_url": post.image_url,
            "likes_count": likes_count,
            "comments_count": comments_count,
            "is_liked": is_liked,
            "created_at": post.created_at.isoformat() if post.created_at else None
        })

    return feed_data


@app.post("/api/posts/{post_id}/like")
def toggle_like(
    post_id: str,
    db: Session = Depends(database.get_db),
    current_user: models.User = Depends(auth.get_current_user)
):
    if not current_user:
        raise HTTPException(status_code=401, detail="NOT_AUTHENTICATED")

    post = db.query(models.Post).filter(models.Post.id == post_id).first()
    if not post:
        raise HTTPException(status_code=404, detail="POST_NOT_FOUND")

    is_liked = False
    if hasattr(models, "PostLike"):
        like_entry = db.query(models.PostLike).filter(
            and_(models.PostLike.post_id == post_id, models.PostLike.user_id == current_user.user_id)
        ).first()

        if like_entry:
            db.delete(like_entry)
            post.likes_count = max(0, (post.likes_count or 1) - 1)
            is_liked = False
        else:
            new_like = models.PostLike(
                id=str(uuid.uuid4()),
                post_id=post_id,
                user_id=current_user.user_id
            )
            db.add(new_like)
            post.likes_count = (post.likes_count or 0) + 1
            is_liked = True

        db.commit()

    return {"likes_count": getattr(post, "likes_count", 0), "is_liked": is_liked}