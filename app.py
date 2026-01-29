import logging
import os
import yt_dlp
import pathlib
from flask import Flask, session, abort, redirect, request, jsonify, url_for, current_app, render_template, flash
from flask_cors import CORS
from moviepy.editor import AudioFileClip, VideoFileClip
from pytube import YouTube
import subprocess
from werkzeug.utils import secure_filename
import assemblyai as aai
import google.generativeai as genai
import pymongo
from google_auth_oauthlib.flow import Flow
from google.oauth2 import id_token
import google.auth.transport.requests
import cachecontrol
from bson import ObjectId
from urllib.parse import urlparse, parse_qs
import requests
import json

# Configure logging
logging.basicConfig(level=logging.DEBUG)

app = Flask("QuickGlance")
CORS(app)

# Database connection
connection_url = "mongodb+srv://QuickGlance:QuickGlance@cluster0.cfmhwiy.mongodb.net/?retryWrites=true&w=majority&ssl=true"
client = pymongo.MongoClient(connection_url)
QuickGlanceDb = client['QuickGlance']
print("Db Connected")

# Collections
summarizations_collection = QuickGlanceDb.Summerizations
users = QuickGlanceDb.users

# Secret key for sessions
app.secret_key = "YourSecretKey"

# Video Upload
UPLOAD_FOLDER = 'uploads'

# Define allowed file extensions
ALLOWED_EXTENSIONS = {'mp4', 'avi', 'mkv', 'mov'}


if not os.path.exists(UPLOAD_FOLDER):
    os.makedirs(UPLOAD_FOLDER, exist_ok=True)
    
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER

# Helper function for allowed files
def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

# Home Route
@app.route("/")
def index():
    if 'user_id' in session:
        return redirect(url_for('home'))
    return render_template("index.html")

# User Registration
@app.route("/register", methods=["GET", "POST"])
def user_register():
    if request.method == "POST":
        username = request.form.get("username")
        password = request.form.get("password")
        email = request.form.get("email")

        if users.find_one({"username": username}):
            return render_template("register.html", error="User already exists")

        new_user = {
            "username": username,
            "password": password,
            "email": email
        }
        users.insert_one(new_user)
        return redirect(url_for('user_login'))

    return render_template("register.html")

# User Login
@app.route("/login", methods=["GET", "POST"])
def user_login():
    if request.method == "POST":
        username = request.form.get("username")
        password = request.form.get("password")

        user = users.find_one({"username": username, "password": password})
        if user:
            session["user_id"] = str(user["_id"])
            session["name"] = user["username"]
            return redirect(url_for('home'))
        else:
            return render_template("login.html", error="Invalid credentials")

    return render_template("login.html")

# Logout Route
@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for('index'))

# Home Route after Login
@app.route("/home", methods=["GET", "POST"])
def home():
    if 'user_id' not in session:
        return redirect(url_for('user_login'))

    if request.method == "POST":
        youtube_url = request.form.get("youtube_url")
        youtube_url = sanitize_youtube_url(youtube_url)

        if not youtube_url:
            return render_template("home.html", error="Invalid YouTube URL")

        result = test_video_processing(youtube_url, "./", "youtube")
        if result:
            logging.info('Video processed successfully')
            insertion_result = summarizations_collection.insert_one(
                {'result': result, 'youtube_url': youtube_url, 'user_id': ObjectId(session["user_id"])}
            )
            return redirect(url_for('summary', result_id=str(insertion_result.inserted_id)))

        logging.error('Video processing failed')
        return render_template("home.html", error="Video processing failed")

    return render_template("home.html", username=session.get("name"))

@app.route('/delete_summary/<summary_id>', methods=['POST'])
def delete_summary(summary_id):
    if 'user_id' not in session:
        flash('You need to be logged in to delete a summary.', 'error')
        return redirect(url_for('user_login'))

    try:
        # Attempt to delete the summary document from the collection
        result = summarizations_collection.delete_one({
            '_id': ObjectId(summary_id),
            'user_id': ObjectId(session["user_id"])  # Ensure the user owns the summary
        })
        
        if result.deleted_count > 0:
            flash('Summary deleted successfully!', 'success')
        else:
            flash('Summary not found or you do not have permission to delete this summary.', 'error')
    except Exception as e:
        flash(f'An error occurred while deleting the summary: {e}', 'error')

    return redirect(url_for('view_summarizations'))  # Redirect back to the view summarizations page


# View Summary
@app.route('/summary/<result_id>', methods=["GET"])
def summary(result_id):
    if 'user_id' not in session:
        return redirect(url_for('user_login'))

    result_document = summarizations_collection.find_one({'_id': ObjectId(result_id), 'user_id': ObjectId(session["user_id"])})

    if result_document:
        return render_template("summary.html", summary=result_document['result'])
    else:
        return render_template("summary.html", error="Result not found")

# Function to fetch YouTube video title using YouTube Data API
def get_youtube_video_title(video_url):
    # Parse the video ID from the URL
    video_id = video_url.split('v=')[-1]
    
    # API call to fetch video details (you will need a YouTube API key)
    api_key = 'AIzaSyCXv2608qMix7HVTGIP4vmKew6bXZsbyKc'
    api_url = f'https://www.googleapis.com/youtube/v3/videos?part=snippet&id={video_id}&key={api_key}'

    response = requests.get(api_url)
    if response.status_code == 200:
        video_info = response.json()
        if video_info["items"]:
            return video_info["items"][0]["snippet"]["title"]
    return "Unknown Title"  # Fallback in case the API fails

# View Past Summarizations
@app.route('/view-summarizations', methods=["GET"])
def view_summarizations():
    if 'user_id' not in session:
        return redirect(url_for('user_login'))

    summarizations = summarizations_collection.find({"user_id": ObjectId(session["user_id"])})

    result = []
    for summary in summarizations:
        video_link = summary.get("youtube_url")
        filename = summary.get("filename", "Unknown Filename")
        
        if video_link:
            video_title = get_youtube_video_title(video_link)  # Get the video title
        else:
            video_title = filename
        
        result.append({
            "text": summary.get("result"), 
            "video_link": video_link, 
            "video_title": video_title,  # Add video title
            "id": str(summary["_id"])
        })
    
    return render_template("view_summarizations.html", summarizations=result)

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

# Function to download audio from YouTube and convert to WAV format
def download_and_convert_audio_from_youtube(youtube_url, output_path):
    try:
        logging.debug(f'Starting download and conversion for URL: {youtube_url}')

        ydl_opts = {
            'format': 'bestaudio/best',
            'outtmpl': os.path.join(output_path, 'output'),
            'postprocessors': [{
                'key': 'FFmpegExtractAudio',
                'preferredcodec': 'wav',
                'preferredquality': '192',
            }],
        }

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info_dict = ydl.extract_info(youtube_url, download=True)
        
        wav_file = os.path.join(output_path, "output.wav")
        logging.debug(f'Output WAV file path: {wav_file}')

        logging.info('Audio downloaded and converted to WAV successfully')
        return wav_file
    except Exception as e:
        logging.error(f'Error during download and conversion: {str(e)}')
        return None

# Function to convert local video file to WAV format
def convert_local_video_to_wav(video_path, output_path):
    try:
        logging.debug(f'Starting conversion for local video: {video_path}')
        wav_file = os.path.join(output_path, "output.wav")

        subprocess.run([
            'ffmpeg', '-i', video_path, '-vn', '-acodec', 'pcm_s16le', '-ar', '44100', '-ac', '2', wav_file
        ], check=True)
        logging.debug('Conversion to WAV completed')

        logging.info('Local video converted to WAV successfully')
        return wav_file
    except Exception as e:
        logging.error(f'Error during local video conversion: {str(e)}')
        return None


def generate_notes_with_gemini(summary):
    try:
        # Define the prompt for the Gemini model
        prompt = f"Generate concise notes from the following summary:\n\n{summary}\n\nNotes:"
        
        # Set up the model with appropriate configuration
        generation_config = {
            "temperature": 0.7,
            "top_p": 0.9,
            "max_output_tokens": 350,  # Limit the number of tokens in the output
        }

        model = genai.GenerativeModel(
            model_name="gemini-1.5-pro-latest",
            generation_config=generation_config
        )

        # Generate content using the Gemini model
        response = model.generate_content(prompt)
        
        # Return the generated notes
        notes =  response.text.strip()
        logging.debug(f'Raw generated content: {response.text.strip()}')
        formatted_notes = notes.replace('. ', '.<br>').replace('! ', '!<br>').replace('? ', '?<br>')

        logging.debug(f'Generated content: {formatted_notes}')

        # Return the formatted notes
        return formatted_notes
    
    
    except Exception as e:
        logging.error(f"An error occurred while generating notes: {str(e)}")
        return "Error in generating notes."

def format_summary(summary):
    """Format the summary to ensure each sentence is on a new line."""
    formatted_summary = summary.replace('. ', '.<br>').replace('! ', '!<br>').replace('? ', '?<br>')
    return formatted_summary



@app.errorhandler(404)
def page_not_found(e):
    return jsonify({'error': 'Page not found'}), 404

@app.errorhandler(500)
def internal_error(e):
    return jsonify({'error': 'Internal server error'}), 500

# Main function for processing
def test_video_processing(input_path, output_path, choice):
    logging.debug('Starting main process')
    output_path = "./OUTPUT"

    try:
        if choice == "youtube":
            logging.debug(f'Checking if the input is a URL: {input_path}')
            logging.debug('Input is a URL. Downloading and converting audio from YouTube.')
            wav_file = download_and_convert_audio_from_youtube(input_path, output_path)
        elif choice == "upload":
            logging.debug(f'File Uploaded: {input_path}')
            logging.debug('Local Video Processing. Converting audio from local video.')
            wav_file = convert_local_video_to_wav(input_path, output_path)

        if wav_file:
            logging.debug(f'WAV file created: {wav_file}')

            logging.debug('Configuring AssemblyAI API')
            aai.settings.api_key = "95869ed680b04600a079c79f69695973"
            transcriber = aai.Transcriber()

            logging.debug('Starting transcription')
            transcript = transcriber.transcribe(wav_file)
            logging.debug(f'Transcription status: {transcript.status}')

            if transcript.status == aai.TranscriptStatus.error:
                logging.error(f'Transcription error: {transcript.error}')
            else:
                logging.info(f'Transcription text: {transcript.text}')

                logging.debug('Configuring Google Generative AI API')
                genai.configure(api_key="AIzaSyCk_nEm0_J4gE20cw4ronBPPF-TtOUqS8g")
                generation_config = {
                    "temperature": 1,
                    "top_p": 0.95,
                    "top_k": 0,
                    "max_output_tokens": 8192,
                }
                safety_settings = [
                    {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_MEDIUM_AND_ABOVE"},
                    {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "BLOCK_MEDIUM_AND_ABOVE"},
                    {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "BLOCK_MEDIUM_AND_ABOVE"},
                    {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_MEDIUM_AND_ABOVE"},
                ]
                model = genai.GenerativeModel(
                    model_name="gemini-1.5-pro-latest",
                    generation_config=generation_config,
                    safety_settings=safety_settings
                )

                logging.debug('Generating summary based on transcription')
                prompt_parts = [
                    f"input: Lecture=\"\"\"{transcript.text}\"\"\"",
                    "output: ",
                    "input: create some notes with other understanding key points to study",
                    "output: ",
                ]
                response = model.generate_content(prompt_parts)
                logging.debug(f'Generated content: {response.text}')
                
                logging.debug('Generating notes based on summary')
                notes = generate_notes_with_gemini(response.text)
                logging.debug(f'Generated content: {notes}')
                logging.debug('Cleaning up: removing WAV file')
                os.remove(wav_file)
                
            return response.text
                    
    
        else:
            logging.error('Audio conversion failed. Please check the input and try again.')
            return "else Block"
    except Exception as e:
        logging.error(f'An error occurred in the main process: {str(e)}')
        
            
# Route for file uploads
@app.route('/upload', methods=['POST'])
def upload_file():
    logging.debug("Upload endpoint hit.")
    
    if 'fileInput' not in request.files:
        logging.error("No file part in request.")
        return jsonify({"error": "No file part"}), 400

    file = request.files['fileInput']
    
    if file.filename == '':
        logging.error("No selected file.")
        return jsonify({"error": "No selected file"}), 400

    if file and allowed_file(file.filename):
        logging.debug(f"File {file.filename} is allowed.")
        filename = secure_filename(file.filename)
        file_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        file.save(file_path)  # Save the uploaded file
        logging.debug(f"File saved to {file_path}")

        # Call the test_video_processing function
        choice = "upload"
        try:
            summary = test_video_processing(file_path, app.config['UPLOAD_FOLDER'], choice)
            logging.debug(f"Processing completed. Summary: {summary}")
            # Save summary to the database
            insertion_result = summarizations_collection.insert_one(
                {'result': summary, 'user_id': ObjectId(session["user_id"])}
            )
            logging.info(f'Summary saved to database with ID: {insertion_result.inserted_id}')
           
        except Exception as e:
            logging.error(f"Error during processing: {str(e)}")
            return jsonify({"error": "File processing failed"}), 500

        return jsonify({"summary": summary})

    logging.error("File type not allowed.")
    return jsonify({"error": "File type not allowed"}), 400

@app.route('/upload.html')
def upload_page():
    return render_template('upload.html') 

@app.route("/display_notes", methods=["POST", "GET"])
def display_notes():
    summary = request.json.get("summary", "")

    if not summary:
        return jsonify({"error": "No summary provided"}), 400

    try:
        notes = generate_notes_with_gemini(summary)
        return jsonify({"notes": notes}) # Render the notes on a new page
    except ValueError as ve:
            print(f"ValueError: {ve}")  # Log specific validation error
            return jsonify({'error': str(ve)}), 500

@app.route("/notes")
def notes_page():
    return render_template("notes.html", notes="")  

@app.route("/process_video", methods=["POST"])
def process_video():
    input_path = request.json.get("input_path", "")
    output_path = request.json.get("output_path", "")
    choice = request.json.get("choice", "")
    
    summary = test_video_processing(input_path, output_path, choice)
    
    if summary:
        # Format summary for HTML rendering
        formatted_summary = format_summary(summary["summary"])
        return render_template("summary.html", summary=formatted_summary)  # Pass formatted summary to the template
    else:
        return jsonify({"error": "Failed to process video"}), 500

#chatbot
# Load the generative model
model = genai.GenerativeModel("gemini-1.5-flash")
genai.configure(api_key=os.getenv("API_KEY"))

@app.route("/chat", methods=["POST"])
def chat():
    print("Received request")  # Add this line
    user_message = request.json.get("message", "")
    
    if not user_message:
        return jsonify({"error": "No message provided"}), 400

    try:
        # Generate response from Gemini model
        response = model.generate_content(user_message)
        return jsonify({"response": response.text}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500    

# Utility function to sanitize and extract YouTube URL
def sanitize_youtube_url(url):
    try:
        parsed_url = urlparse(url)
        if parsed_url.hostname in ["www.youtube.com", "youtube.com", "youtu.be"]:
            if parsed_url.path == "/watch" and "v" in parse_qs(parsed_url.query):
                return f"https://www.youtube.com/watch?v={parse_qs(parsed_url.query)['v'][0]}"
            elif parsed_url.hostname == "youtu.be":
                return f"https://www.youtube.com/watch?v={parsed_url.path[1:]}"
            elif parsed_url.path and parsed_url.hostname == "www.youtube.com":
                # For URLs like https://www.youtube.com/shorts/...
                video_id = parsed_url.path.split('/')[-1]
                return f"https://www.youtube.com/watch?v={video_id}"
    except Exception as e:
        logging.error(f'Invalid YouTube URL: {str(e)}')
    return None

if __name__ == "__main__":
    logging.basicConfig(level=logging.DEBUG)
    app.run(debug=True)
