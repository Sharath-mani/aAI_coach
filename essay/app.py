from flask import Flask, request, jsonify, render_template, send_from_directory, send_file
from flask_cors import CORS
from datetime import datetime
import json
import sys
import os
import google.generativeai as genai
from io import BytesIO

# Import your essay evaluation modules
from essaysam1 import generate_pass_certificate, generate_detailed_report_pdf, get_next_certificate_number

from fastapi import FastAPI
from fastapi.responses import FileResponse

app = FastAPI()

@app.get("/")
def serve_frontend():
    return FileResponse("index.html")

# Get the directory where app.py is located
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# Configure Gemini API
# Configure Gemini API using environment variable
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

if GEMINI_API_KEY and GEMINI_API_KEY.strip():
    print("✅ Gemini API Key loaded successfully")
    genai.configure(api_key=GEMINI_API_KEY)
    MODEL_NAME = "gemini-2.5-flash"
else:
    print("⚠️ Warning: GEMINI_API_KEY not set. Using mock generation for questions.")
    MODEL_NAME = None
print(GEMINI_API_KEY)

app = Flask(__name__, 
            static_folder=BASE_DIR,
            static_url_path='',
            template_folder=BASE_DIR)
CORS(app)

# Store evaluation results
evaluations = {}

# Valid evaluation parameters (matches essaysam1.py)
VALID_EVAL_PARAMS = [
    "language", "analysis", "thought", "structure", "relevance",
    "creativity", "argumentation", "coherence", "grammar", "vocabulary",
    "depth", "originality", "clarity", "logic", "persuasiveness",
    "evidence", "examples", "organization", "flow", "transitions",
    "conclusion", "introduction", "thesis", "tone", "style",
    "mechanics", "syntax", "spelling", "punctuation", "accuracy",
    "insight", "critical thinking", "argument strength", "topic adherence"
]

# Serve frontend files
@app.route('/')
def index():
    """Serve the main HTML file"""
    return send_from_directory(BASE_DIR, 'index.html')

@app.route('/<path:filename>')
def serve_static(filename):
    """Serve static files (CSS, JS, etc.)"""
    return send_from_directory(BASE_DIR, filename)

@app.route('/api/evaluate', methods=['POST'])
def evaluate_essay():
    """
    Endpoint to evaluate an essay
    Expected JSON payload:
    {
        "candidateName": "John Doe",
        "essayTopic": "Climate Change",
        "essay": "essay content here...",
        "evaluationParams": ["grammar", "clarity", "structure", ...]
    }
    """
    try:
        data = request.get_json()
        
        # Validate input
        if not data:
            return jsonify({'success': False, 'message': 'No data provided'}), 400
            
        required_fields = ['candidateName', 'essayTopic', 'essay', 'evaluationParams']
        for field in required_fields:
            if field not in data:
                return jsonify({'success': False, 'message': f'Missing field: {field}'}), 400
        
        # Extract data
        candidate_name = data['candidateName'].strip()
        essay_topic = data['essayTopic'].strip()
        essay = data['essay'].strip()
        eval_params = data['evaluationParams']
        
        # Validate essay length
        if len(essay.split()) < 100:
            return jsonify({'success': False, 'message': 'Essay must be at least 100 words'}), 400
        
        # Call your essay evaluation function from essay2.py
        # For now, this is a placeholder - replace with actual implementation
        evaluation_result = perform_evaluation(
            candidate_name=candidate_name,
            essay_topic=essay_topic,
            essay=essay,
            eval_params=eval_params
        )
        
        # Store the evaluation
        eval_id = f"eval_{len(evaluations) + 1}_{datetime.now().timestamp()}"
        evaluations[eval_id] = {
            'candidateName': candidate_name,
            'essayTopic': essay_topic,
            'timestamp': datetime.now().isoformat(),
            'result': evaluation_result
        }
        
        return jsonify({
            'success': True,
            'evaluationId': eval_id,
            'data': evaluation_result
        }), 200
        
    except Exception as e:
        print(f"Error in evaluate_essay: {str(e)}")
        return jsonify({'success': False, 'message': str(e)}), 500

@app.route('/api/evaluations', methods=['GET'])
def get_evaluations():
    """Get all evaluations"""
    try:
        return jsonify({'success': True, 'evaluations': evaluations}), 200
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500

@app.route('/api/evaluations/<eval_id>', methods=['GET'])
def get_evaluation(eval_id):
    """Get a specific evaluation"""
    try:
        if eval_id not in evaluations:
            return jsonify({'success': False, 'message': 'Evaluation not found'}), 404
        
        return jsonify({'success': True, 'evaluation': evaluations[eval_id]}), 200
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500

@app.route('/api/health', methods=['GET'])
def health_check():
    """Health check endpoint"""
    return jsonify({'status': 'healthy', 'timestamp': datetime.now().isoformat()}), 200

@app.route('/api/valid-parameters', methods=['GET'])
def get_valid_parameters():
    """Get list of valid evaluation parameters"""
    return jsonify({
        'success': True,
        'parameters': VALID_EVAL_PARAMS
    }), 200

@app.route('/api/generate-questions', methods=['POST'])
def generate_questions():
    """
    Generate guiding questions based on essay topic
    Expected JSON: { "topic": "essay topic" }
    """
    try:
        data = request.get_json()
        if not data or 'topic' not in data:
            return jsonify({'success': False, 'message': 'Topic required'}), 400
        
        topic = data['topic'].strip()
        if len(topic) < 3:
            return jsonify({'success': False, 'message': 'Topic too short'}), 400
        
        # Generate questions using Gemini API
        questions = generate_essay_questions(topic)
        
        return jsonify({
            'success': True,
            'topic': topic,
            'questions': questions
        }), 200
        
    except Exception as e:
        print(f"Error generating questions: {str(e)}")
        return jsonify({'success': False, 'message': str(e)}), 500

def perform_evaluation(candidate_name, essay_topic, essay, eval_params):
    """
    Perform essay evaluation using your Gemini API integration
    Replace this with your actual evaluation function from essay2.py
    """
    
    # This is a placeholder implementation
    # In production, call your actual evaluation function:
    # from essay2 import evaluate_essay_with_gemini
    # return evaluate_essay_with_gemini(essay, eval_params)
    
    import random
    
    # Generate mock scores for demonstration
    scores = {}
    essay_words = len(essay.split())
    base_score = min(100, 40 + (essay_words / 10))
    
    for param in eval_params:
        variance = random.uniform(-10, 10)
        scores[param] = max(50, min(100, int(base_score + variance)))
    
    # Calculate overall score
    overall_score = int(sum(scores.values()) / len(scores))
    
    # Generate feedback
    feedback = f"Your essay ({essay_words} words) on '{essay_topic}' has been evaluated. "
    feedback += f"Overall score: {overall_score}/100. "
    
    if overall_score >= 80:
        feedback += "Excellent work!"
    elif overall_score >= 70:
        feedback += "Good performance."
    elif overall_score >= 60:
        feedback += "Fair attempt. Room for improvement."
    else:
        feedback += "Consider reviewing the feedback to improve."
    
    # Determine pass/fail
    passed = overall_score >= 70
    cert_number = f"AGAI{int(datetime.now().timestamp() * 1000) % 1000000:06d}" if passed else None
    
    return {
        'candidateName': candidate_name,
        'essayTopic': essay_topic,
        'overallScore': overall_score,
        'scores': scores,
        'feedback': feedback,
        'strengths': [f"Good {param}" for param in sorted(scores.items(), key=lambda x: x[1], reverse=True)[:3]],
        'weaknesses': [f"Improve {param}" for param in sorted(scores.items(), key=lambda x: x[1])[:3]],
        'suggestions': [
            {'category': param, 'suggestion': f'Try to enhance your {param} further.'}
            for param in eval_params[:3]
        ],
        'passed': passed,
        'certificateNumber': cert_number
    }

def generate_essay_questions(topic):
    """
    Generate guiding questions for the essay topic
    Uses Gemini API if available, otherwise generates mock questions
    """
    if MODEL_NAME:
        try:
            model = genai.GenerativeModel(MODEL_NAME)
            prompt = f"""Generate 4-5 specific, thought-provoking questions that would help guide someone writing an essay about: "{topic}"

Requirements:
- Questions should be open-ended
- Focus on analysis, critical thinking, and depth
- Be specific to the topic
- Range from basic to advanced levels

Return ONLY the questions, one per line, without numbering or bullet points."""
            
            response = model.generate_content(prompt)
            questions_text = response.text.strip()
            questions = [q.strip() for q in questions_text.split('\n') if q.strip()]
            return questions[:5]  # Return max 5 questions
            
        except Exception as e:
            print(f"Gemini API error in generate_essay_questions: {e}")
            # Fallback to mock questions
            return generate_mock_questions(topic)
    else:
        return generate_mock_questions(topic)

def generate_mock_questions(topic):
    """Generate mock questions if API is unavailable"""
    topic_lower = topic.lower()
    
    # Extract key concepts from topic
    keywords = topic_lower.split()
    
    mock_questions = [
        f"What is the main purpose or significance of discussing {topic}?",
        f"How does {topic} impact society, individuals, or the broader context?",
        f"What are the key arguments for and against {topic}?",
        f"Can you provide specific examples or evidence related to {topic}?",
        f"What are the future implications or conclusions regarding {topic}?"
    ]
    
    return mock_questions

@app.route('/api/generate-certificate', methods=['POST'])
def generate_certificate():
    """
    Generate and return a certificate PDF
    Expected JSON payload:
    {
        "candidate_name": "John Doe",
        "scores_percent": {"grammar": 85.5, "structure": 90.0, ...},
        "essay_topic": "Climate Change"
    }
    """
    try:
        data = request.get_json()
        
        if not data:
            return jsonify({'success': False, 'message': 'No data provided'}), 400
        
        required_fields = ['candidate_name', 'scores_percent']
        for field in required_fields:
            if field not in data:
                return jsonify({'success': False, 'message': f'Missing field: {field}'}), 400
        
        candidate_name = data['candidate_name'].strip()
        scores_percent = data['scores_percent']
        essay_topic = data.get('essay_topic', None)
        
        # Validate scores_percent
        if not isinstance(scores_percent, dict) or not scores_percent:
            return jsonify({'success': False, 'message': 'scores_percent must be a non-empty dictionary'}), 400
        
        # Get next certificate number
        certificate_number = get_next_certificate_number()
        
        # Generate PDF to BytesIO
        pdf_buffer = BytesIO()
        temp_filename = f"temp_cert_{certificate_number}.pdf"
        
        try:
            # Generate certificate
            generate_pass_certificate(
                candidate_name=candidate_name,
                scores_percent=scores_percent,
                essay_topic=essay_topic,
                certificate_number=certificate_number,
                filename=temp_filename,
                logo_path="logo.png"
            )
            
            # Read the generated PDF into BytesIO
            with open(temp_filename, 'rb') as f:
                pdf_buffer.write(f.read())
            pdf_buffer.seek(0)
            
            # Clean up temp file
            if os.path.exists(temp_filename):
                os.remove(temp_filename)
            
            # Return PDF as binary response
            return send_file(
                pdf_buffer,
                mimetype='application/pdf',
                as_attachment=True,
                download_name=f"{candidate_name}_certificate.pdf"
            )
            
        except Exception as e:
            print(f"Error generating certificate PDF: {str(e)}")
            if os.path.exists(temp_filename):
                os.remove(temp_filename)
            raise
            
    except Exception as e:
        print(f"Error in generate_certificate: {str(e)}")
        return jsonify({'success': False, 'message': str(e)}), 500

@app.route('/api/generate-report', methods=['POST'])
def generate_report():
    """
    Generate and return a detailed report PDF
    Expected JSON payload:
    {
        "candidate_name": "John Doe",
        "session_history": [
            {
                "scores": {"grammar": 8, "structure": 9, ...},
                "feedbacks": {"grammar": "feedback text", ...},
                "avg_score": 8.5,
                "essay_topic": "Climate Change",
                "essay_question": "Why is climate...",
                "essay": "essay text...",
                "overall_feedback": "Great work!",
                "timestamp": 1234567890
            },
            ...
        ]
    }
    """
    try:
        data = request.get_json()
        
        if not data:
            return jsonify({'success': False, 'message': 'No data provided'}), 400
        
        required_fields = ['candidate_name', 'session_history']
        for field in required_fields:
            if field not in data:
                return jsonify({'success': False, 'message': f'Missing field: {field}'}), 400
        
        candidate_name = data['candidate_name'].strip()
        session_history = data['session_history']
        
        # Validate session_history
        if not isinstance(session_history, list):
            return jsonify({'success': False, 'message': 'session_history must be a list'}), 400
        
        # Generate PDF to BytesIO
        pdf_buffer = BytesIO()
        temp_filename = f"temp_report_{int(datetime.now().timestamp())}.pdf"
        
        try:
            # Generate detailed report
            generate_detailed_report_pdf(
                session_history=session_history,
                candidate_name=candidate_name,
                filename=temp_filename
            )
            
            # Read the generated PDF into BytesIO
            with open(temp_filename, 'rb') as f:
                pdf_buffer.write(f.read())
            pdf_buffer.seek(0)
            
            # Clean up temp file
            if os.path.exists(temp_filename):
                os.remove(temp_filename)
            
            # Return PDF as binary response
            return send_file(
                pdf_buffer,
                mimetype='application/pdf',
                as_attachment=True,
                download_name=f"{candidate_name}_essay_report.pdf"
            )
            
        except Exception as e:
            print(f"Error generating report PDF: {str(e)}")
            if os.path.exists(temp_filename):
                os.remove(temp_filename)
            raise
            
    except Exception as e:
        print(f"Error in generate_report: {str(e)}")
        return jsonify({'success': False, 'message': str(e)}), 500


    # Run the Flask app
    # Change debug=False for production
    app.run(debug=True, host='127.0.0.1', port=5000)
