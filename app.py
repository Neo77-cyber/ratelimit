from fastapi import FastAPI, Depends, HTTPException, status, Request
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from passlib.context import CryptContext
from datetime import datetime, timedelta
import jwt
from tortoise import fields
from tortoise.models import Model
from tortoise.contrib.fastapi import register_tortoise
from pydantic import BaseModel
from datetime import datetime
from typing import Dict
from fastapi import FastAPI, Depends, HTTPException
from redis_utils import limiter, MAX_LOGIN_ATTEMPTS, RATE_LIMIT_DURATION
import asyncpg
import redis





app = FastAPI()

SECRET_KEY = "FMe1o0baNLQ_ntPVuK2FTGWwxc_m1KfuKWp0xgReaJg"

ACCESS_TOKEN_EXPIRE_MINUTES = 1440

password_context = CryptContext(schemes=['bcrypt'], deprecated = "auto")   


class User(Model):
    id = fields.IntField(pk=True)
    username = fields.CharField(max_length = 100)
    password_hash = fields.CharField(max_length = 100)
    

    async def verify_password(self, plain_password):
        return password_context.hash(plain_password)
    
    class PydanticMeta:
        exclude = ["password_hash"]

class Profile(Model):
    id = fields.IntField(pk=True)
    user = fields.OneToOneField("models.User", on_delete=fields.CASCADE, related_name="profile")
    first_name = fields.CharField(max_length = 100)
    surname = fields.CharField(max_length = 100)
    email_address = fields.CharField(max_length= 100)
    gender = fields.CharField(max_length= 100)

class ProfileCreateRequest(BaseModel):
    first_name: str
    surname: str
    email_address: str
    gender: str
    
class ProfileResponse(BaseModel):
    user: str
    first_name: str
    surname: str
    email_address: str
    gender: str


register_tortoise(
    app,
    db_url='postgres://fuxmvfac:LUWPKc70O8FSw533lTnvntiZV6_3VSAw@snuffleupagus.db.elephantsql.com/fuxmvfac',
    modules={'models': ['app']},
    generate_schemas=True,
    add_exception_handlers=True,
)

async def get_user(username: str):
    return await User.get_or_none(username=username)

def verify_password(plain_password, hashed_password):
    return password_context.verify(plain_password, hashed_password)

def authenticate_user(user:User, password: str):
    if not user or not verify_password(password, user.password_hash):
        return False
    else:
        return user
    
def create_access_token(data: dict, expires_delta: timedelta):
    to_encode = data.copy()
    expire = datetime.utcnow() + expires_delta
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm="HS256")
    return encoded_jwt




oauth2_scheme = OAuth2PasswordBearer(tokenUrl="token")

redis_client = redis.Redis(host="redis-server")








@app.post('/register')
async def register(username:str, password:str):
    existing_user = await get_user(username)
    if existing_user:
        raise HTTPException(status_code = 400, detail = 'Username already exists')
    hashed_password = password_context.hash(password)
    user = await User.create(username=username, password_hash = hashed_password)
    return {"message": "Registered successfuly"}

@app.post('/login')
async def login(form_data: OAuth2PasswordRequestForm = Depends()):
    user = await get_user(form_data.username)
    if not user or not await user.verify_password(form_data.password):
        
        redis_key = f"login_attempts:{form_data.username}"
        attempts = redis_client.incr(redis_key)
        redis_client.expire(redis_key, RATE_LIMIT_DURATION.seconds)

        if attempts >= MAX_LOGIN_ATTEMPTS:
            
            retry_after = redis_client.ttl(redis_key)
            raise HTTPException(
                status_code=429,
                detail=f"Too many login attempts. Please try again after {retry_after} seconds.",
                headers={"Retry-After": str(retry_after)}
            )
        else:
            
            raise HTTPException(status_code=401, detail="Invalid username or password.")
    
    
    redis_key = f"login_attempts:{form_data.username}"
    redis_client.delete(redis_key)

    
    access_token_expires = timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    access_token = create_access_token({"sub": user.username}, access_token_expires)
    return {"access_token": access_token, "token_type": "bearer"}

    
@app.get("/protected")
async def protected_route(token: str = Depends(oauth2_scheme)):
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=["HS256"])
        username = payload.get("sub")
        user = await get_user(username)
        if not user:
            raise HTTPException(status_code=401, detail="Invalid token.")
        return {"message": "Protected route accessed successfully."}
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token has expired.")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Invalid token.")
    
@app.post("/token")
async def generate_token(form_data: OAuth2PasswordRequestForm = Depends()):
    user = await get_user(form_data.username)
    if not user or not await user.verify_password(form_data.password):
        raise HTTPException(status_code=401, detail="Invalid username or password.")
    access_token_expires = timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    access_token = create_access_token({"sub": user.username}, access_token_expires)
    return {"access_token": access_token, "token_type": "bearer"}

@app.post("/create_profile", response_model = ProfileResponse )
async def create_profile(new_profile:ProfileCreateRequest, token:str = Depends(oauth2_scheme)):
    try:
            payload = jwt.decode(token, SECRET_KEY, algorithms=["HS256"])
            username = payload.get("sub")
            user = await get_user(username)
            if not user:
                raise HTTPException(status_code=401, detail="Invalid token.")
            create_profile = await Profile.create(user=user, first_name=new_profile.first_name, surname = new_profile.surname,
                                                  email_address = new_profile.email_address, gender = new_profile.gender
                                        
                                                    )

            response = ProfileResponse(user=user.username, first_name=new_profile.first_name, surname = new_profile.surname,
                                                  email_address = new_profile.email_address, gender = new_profile.gender  )
            return response

    except jwt.ExpiredSignatureError:
            raise HTTPException(status_code=401, detail="Token has expired.")
    except jwt.InvalidTokenError:
            raise HTTPException(status_code=401, detail="Invalid token.")
    
@app.get('/userprofile')
async def user_profile(request: Request, token:str = Depends(oauth2_scheme)):
    try:
            payload = jwt.decode(token, SECRET_KEY, algorithms=["HS256"])
            username = payload.get("sub")
            user = await get_user(username)
            if not user:
                raise HTTPException(status_code=401, detail="Invalid token.")
            
            clientIp = request.client.host
            res = limiter(clientIp, 5)
            if res["call"]:

                userprofile =  await Profile.filter(user=user)
                if not userprofile:
                    raise HTTPException(status_code=404, detail="Profile not found.")
                return userprofile 
            else:
                 raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail={
                "message": "call limit reached",
                    })      
    except jwt.ExpiredSignatureError:
            raise HTTPException(status_code=401, detail="Token has expired.")
    except jwt.InvalidTokenError:
            raise HTTPException(status_code=401, detail="Invalid token.") 






   