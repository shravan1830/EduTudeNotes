import os
from flask import Flask, request, render_template
from werkzeug.utils import secure_filename
from speech_recognition import Recognizer, AudioFile
from app import summarize_text  # Your existing summarization code

app = Flask(__name__)
UPLOAD_FOLDER = 'uploads/'
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER

# Route to upload and process file
@app.route('/upload', methods=['GET', 'POST'])
def upload_file():
    if request.method == 'POST':
        # Check if the post request has the file
        if 'file' not in request.files:
            return 'No file part'
        file = request.files['file']
        if file.filename == '':
            return 'No selected file'
        
        # Save the file
        filename = secure_filename(file.filename)
        file_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        file.save(file_path)

        # Convert the uploaded lecture to text
        recognizer = Recognizer()
        with AudioFile(file_path) as source:
            audio_data = recognizer.record(source)
            transcript = recognizer.recognize_google(audio_data)
        
        # Summarize the transcript
        summary = summarize_text(transcript)

        # Render the results
        return render_template('summary.html', transcript=transcript, summary=summary)

    return '''
    <form method="post" enctype="multipart/form-data">
        <input type="file" name="file">
        <input type="submit" value="Upload">
    </form>
    '''

if __name__ == '__main__':
    app.run(debug=True)
