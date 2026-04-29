# Essay Evaluation API

A FastAPI-based service for evaluating essays using Google's Gemini AI. This API provides endpoints for essay evaluation, question generation, and certificate/report generation.

## Features

- **Essay Evaluation**: Evaluate essays on multiple parameters (language, structure, creativity, etc.)
- **Question Generation**: Generate essay questions based on topics or themes
- **Certificate Generation**: Create PDF certificates for high-performing essays
- **Detailed Reports**: Generate comprehensive PDF reports for evaluation sessions

## Installation

1. Install dependencies:
```bash
pip install -r requirements.txt
```

2. Set up your Gemini API key:
```bash
python setup_api_key.py
```
Or manually edit the `.env` file:
```bash
# Edit .env file and replace with your actual API key
GEMINI_API_KEY=your-actual-api-key-here
```

## Running the API

```bash
python fastapi_app.py
```

The API will be available at `http://localhost:8000`

## API Endpoints

### GET /
Returns basic API information.

### GET /valid-parameters
Returns the list of valid evaluation parameters.

**Response:**
```json
{
  "parameters": [
    "accuracy",
    "analysis",
    "argument strength",
    "argumentation",
    "clarity",
    "coherence",
    "conclusion",
    "creativity",
    "critical thinking",
    "depth",
    "evidence",
    "examples",
    "flow",
    "grammar",
    "insight",
    "introduction",
    "language",
    "logic",
    "mechanics",
    "organization",
    "originality",
    "persuasiveness",
    "punctuation",
    "relevance",
    "spelling",
    "structure",
    "style",
    "syntax",
    "thesis",
    "thought",
    "tone",
    "topic adherence",
    "transitions",
    "vocabulary"
  ]
}
```

### POST /evaluate
Evaluate an essay on specified parameters.

**Request Body:**
```json
{
  "essay": "Your essay text here...",
  "parameters": ["structure", "grammar", "creativity"],
  "candidate_name": "John Doe"
}
```

**Response:**
```json
{
  "scores": {
    "structure": 8.5,
    "grammar": 9.0,
    "creativity": 7.5
  },
  "feedbacks": {
    "structure": "Good organization with clear paragraphs.",
    "grammar": "Excellent grammar with no errors.",
    "creativity": "Creative ideas but could be more original."
  },
  "avg_score": 8.33,
  "overall_feedback": "Average score: 8.33/10. Excellent work!",
  "essay_topic": "Technology in Education",
  "candidate_name": "John Doe"
}
```

### POST /generate-question
Generate an essay question based on a topic or theme.

**Request Body:**
```json
{
  "basis": "climate change"
}
```

**Response:**
```json
{
  "question": "Discuss the impact of climate change on global agriculture and propose sustainable solutions."
}
```

### POST /generate-certificate
Generate a PDF certificate for outstanding performance.

**Request Body:**
```json
{
  "candidate_name": "John Doe",
  "scores_percent": {
    "structure": 85.0,
    "grammar": 90.0,
    "creativity": 75.0
  },
  "essay_topic": "Technology in Education"
}
```

**Response:** PDF file download

### POST /generate-report
Generate a detailed PDF report for a session.

**Request Body:**
```json
{
  "candidate_name": "John Doe",
  "session_history": [
    {
      "candidate_name": "John Doe",
      "essay_question": "Discuss the role of technology in modern education.",
      "essay_topic": "Technology in Education",
      "essay": "Essay text...",
      "scores": {"structure": 8.5, "grammar": 9.0},
      "feedbacks": {"structure": "Good structure", "grammar": "Excellent"},
      "avg_score": 8.75,
      "overall_feedback": "Great work!",
      "timestamp": 1704067200.0
    }
  ]
}
```

**Response:** PDF file download

## Usage Examples

### Python Client

```python
import requests

# Evaluate an essay
response = requests.post("http://localhost:8000/evaluate", json={
    "essay": "Technology is transforming education...",
    "parameters": ["structure", "grammar", "creativity"],
    "candidate_name": "John Doe"
})
result = response.json()
print(f"Average Score: {result['avg_score']}")

# Generate a question
response = requests.post("http://localhost:8000/generate-question", json={
    "basis": "artificial intelligence"
})
question = response.json()["question"]
print(f"Generated Question: {question}")
```

### cURL Examples

```bash
# Get valid parameters
curl http://localhost:8000/valid-parameters

# Evaluate essay
curl -X POST "http://localhost:8000/evaluate" \
     -H "Content-Type: application/json" \
     -d '{
       "essay": "Your essay text here...",
       "parameters": ["structure", "grammar"],
       "candidate_name": "John Doe"
     }'

# Generate question
curl -X POST "http://localhost:8000/generate-question" \
     -H "Content-Type: application/json" \
     -d '{"basis": "climate change"}'
```

## Certificate Generation

Certificates are automatically generated when all evaluation categories score 70% or above. The certificate includes:

- Candidate name
- Achievement description
- Score breakdown table
- Performance overview charts
- Certificate number
- Digital signature

## Report Generation

Detailed reports include:

- Session summary table
- Individual essay evaluations
- Score breakdowns
- Detailed feedback
- Performance trends

## Error Handling

The API returns appropriate HTTP status codes:

- `200`: Success
- `400`: Bad request (invalid parameters, empty essay, etc.)
- `500`: Internal server error (AI API failures, etc.)

Error responses include a `detail` field with the error message.

## Dependencies

- FastAPI: Web framework
- Uvicorn: ASGI server
- Pydantic: Data validation
- Google Generative AI: AI evaluation
- ReportLab: PDF generation
- Python-dotenv: Environment variable management

## Environment Variables

- `GEMINI_API_KEY`: Your Google Gemini API key (required)

## Testing the API

Run the test script to verify the API is working:

```bash
python test_api.py
```

This will test all the main endpoints and report any issues.

## Development

To run in development mode with auto-reload:

```bash
uvicorn fastapi_app:app --reload --host 0.0.0.0 --port 8000
```

API documentation is automatically available at `http://localhost:8000/docs` (Swagger UI) and `http://localhost:8000/redoc` (ReDoc).