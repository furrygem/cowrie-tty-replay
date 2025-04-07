import os
import boto3
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from dotenv import load_dotenv
import struct
import io
import time
from pathlib import Path
import logging

logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

load_dotenv()

app = FastAPI(title="TTY Session Viewer")
app.mount("/static", StaticFiles(directory="static"), name="static")

s3 = boto3.client(
    's3',
    aws_access_key_id=os.getenv('AWS_ACCESS_KEY_ID'),
    aws_secret_access_key=os.getenv('AWS_SECRET_ACCESS_KEY'),
    region_name=os.getenv('AWS_REGION', 'us-east-1'),
    endpoint_url=os.getenv('ENDPOINT_URL'),
)

BUCKET_NAME = os.getenv('S3_BUCKET_NAME')

OP_OPEN = 1
OP_CLOSE = 2
OP_WRITE = 3
OP_EXEC = 4
TYPE_INPUT = 1
TYPE_OUTPUT = 2
TYPE_INTERACT = 3

templates = Jinja2Templates(directory="templates")

def process_tty_session(session_data, settings):
    """Process a tty session and return raw data with metadata"""
    ssize = struct.calcsize("<iLiiLL")
    currtty, prevtime, prefdir = 0, 0, 0
    output = []
    current_line = []
    
    fd = io.BytesIO(session_data)
    
    while True:
        try:
            (op, tty, length, direction, sec, usec) = struct.unpack(
                "<iLiiLL", fd.read(ssize)
            )
            data = fd.read(length)
        except struct.error as e:
            break

        if currtty == 0:
            currtty = tty

        if str(tty) == str(currtty) and op == OP_WRITE:
            if prefdir == 0:
                prefdir = direction
                if settings["input_only"]:
                    prefdir = TYPE_INPUT
                    if direction == TYPE_INPUT:
                        prefdir = TYPE_OUTPUT
            
            if direction == prefdir or settings["both_dirs"]:
                try:
                    decoded_data = data.decode('utf-8', errors='replace')
                    
                    if direction == TYPE_OUTPUT:
                        output.append({
                            'data': decoded_data,
                            'direction': 'output',
                            'timestamp': float(sec) + float(usec) / 1000000
                        })
                except Exception as e:
                    logger.error(f"Error decoding data: {e}")
                
        elif str(tty) == str(currtty) and op == OP_CLOSE:
            break
    
    return output

@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    """List all available sessions"""
    try:
        response = s3.list_objects_v2(Bucket=BUCKET_NAME)
        sessions = [obj['Key'].split('/')[-1] for obj in response.get('Contents', []) if obj['Key'].startswith('tty_logs/')]
        logger.debug(f"Found {len(sessions)} sessions")
        return templates.TemplateResponse(
            "index.html",
            {"request": request, "sessions": sessions}
        )
    except Exception as e:
        logger.error(f"Error listing sessions: {e}")
        return templates.TemplateResponse(
            "error.html",
            {"request": request, "error": str(e)}
        )

@app.get("/session/{session_name}", response_class=HTMLResponse)
async def view_session(request: Request, session_name: str):
    """View a specific session"""
    try:
        logger.debug(f"Fetching session: {session_name}")
        # Get session from S3
        response = s3.get_object(Bucket=BUCKET_NAME, Key=f"tty_logs/{session_name}")
        session_data = response['Body'].read()
        logger.debug(f"Read {len(session_data)} bytes of session data")
        
        # Process session with default settings
        settings = {
            "tail": 0,
            "maxdelay": 0,
            "input_only": 0,
            "both_dirs": 1,
            "colorify": 1
        }
        
        session_output = process_tty_session(session_data, settings)
        logger.debug(f"Processed {len(session_output)} entries")
        
        return templates.TemplateResponse(
            "session.html",
            {
                "request": request,
                "session_name": session_name,
                "session_data": session_output
            }
        )
    except Exception as e:
        logger.error(f"Error viewing session: {e}")
        return templates.TemplateResponse(
            "error.html",
            {"request": request, "error": str(e)}
        )

@app.get("/api/session/{session_name}")
async def get_session_data(session_name: str):
    """Get raw session data"""
    try:
        response = s3.get_object(Bucket=BUCKET_NAME, Key=f"tty_logs/{session_name}")
        session_data = response['Body'].read()
        
        settings = {
            "tail": 0,
            "maxdelay": 0,
            "input_only": 0,
            "both_dirs": 1,
            "colorify": 1
        }
        
        session_output = process_tty_session(session_data, settings)
        print(session_output)
        return JSONResponse(content=session_output)
    except Exception as e:
        raise HTTPException(status_code=404, detail=str(e))

if __name__ == '__main__':
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000) 