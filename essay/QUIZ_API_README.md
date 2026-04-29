# Quiz Generation API

A FastAPI-based service for generating quizzes dynamically using Google's Gemini AI. This API provides endpoints for quiz generation, answer evaluation, and session reporting.

## Features

- **Dynamic Quiz Generation**: Create quizzes on any topic with customizable difficulty and language
- **Category Management**: Focus on specific subcategories with equal distribution
- **Multi-language Support**: Generate quizzes in English, Kannada, Hindi, and more
- **Answer Evaluation**: Automatic score computation with category-wise performance tracking
- **Session Reporting**: Generate comprehensive PDF reports for quiz sessions
- **RESTful API**: Clean, documented endpoints for easy integration

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
python quiz_fastapi_app.py
```

The API will be available at `http://localhost:8001`

Auto-generated documentation:
- Swagger UI: `http://localhost:8001/docs`
- ReDoc: `http://localhost:8001/redoc`

## API Endpoints

### GET /
Returns basic API information.

**Response:**
```json
{
  "message": "Quiz Generation API",
  "version": "1.0.0"
}
```

### GET /health
Health check endpoint.

**Response:**
```json
{
  "status": "healthy",
  "service": "Quiz Generation API"
}
```

### POST /generate-quiz
Generate a new quiz on a specified topic.

**Request Body:**
```json
{
  "topic": "Python Programming",
  "language": "English",
  "difficulty": "medium",
  "quiz_count": 5,
  "focus_categories": null
}
```

**Parameters:**
- `topic` (string, required): Topic for the quiz
- `language` (string, optional): Language for questions (default: "English")
- `difficulty` (string, optional): "easy", "medium", or "hard" (default: "medium")
- `quiz_count` (integer, optional): Number of questions (default: 5)
- `focus_categories` (array, optional): Specific categories to focus on

**Response:**
```json
{
  "quiz": [
    {
      "question": "[Category: basics] What is a Python list?",
      "options": ["a collection", "a language feature", "a data structure", "all of the above"],
      "answer": "all of the above",
      "explanation": "Python lists are collections of items...",
      "category": "basics"
    }
  ],
  "config": {
    "topic": "Python Programming",
    "language": "English",
    "difficulty": "medium",
    "quiz_count": 5,
    "focus_categories": []
  }
}
```

### POST /evaluate-answers
Evaluate user answers and compute scores.

**Request Body:**
```json
{
  "quiz": [
    {
      "question": "[Category: basics] What is a Python list?",
      "options": ["a collection", "a language feature", "a data structure", "all of the above"],
      "answer": "all of the above",
      "explanation": "Python lists are collections...",
      "category": "basics"
    }
  ],
  "user_answers": ["all of the above"],
  "candidate_name": "John Doe"
}
```

**Response:**
```json
{
  "total_questions": 1,
  "correct_count": 1,
  "score_percent": 100.0,
  "details": [
    {
      "question": "[Category: basics] What is a Python list?",
      "correct_answer": "all of the above",
      "user_answer": "all of the above",
      "is_correct": true,
      "explanation": "Python lists are collections...",
      "category": "basics"
    }
  ],
  "category_stats": {
    "basics": {
      "total": 1,
      "correct": 1
    }
  },
  "category_percentages": {
    "basics": 100.0
  },
  "candidate_name": "John Doe"
}
```

### POST /generate-session-report
Generate a PDF report for quiz session history.

**Request Body:**
```json
{
  "candidate_name": "John Doe",
  "session_history": [
    {
      "config": {
        "topic": "Python",
        "difficulty": "medium",
        "language": "English",
        "quiz_count": 5,
        "focus_categories": []
      },
      "category_stats": {
        "basics": {"total": 3, "correct": 2}
      },
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

# Generate a quiz
response = requests.post("http://localhost:8001/generate-quiz", json={
    "topic": "Python Programming",
    "difficulty": "medium",
    "quiz_count": 5
})
quiz = response.json()

# Evaluate answers
user_answers = ["option1", "option2", "option1", "option3", "option2"]
response = requests.post("http://localhost:8001/evaluate-answers", json={
    "quiz": quiz["quiz"],
    "user_answers": user_answers,
    "candidate_name": "John Doe"
})
result = response.json()
print(f"Score: {result['score_percent']:.2f}%")
```

### cURL Examples

```bash
# Generate quiz
curl -X POST "http://localhost:8001/generate-quiz" \
     -H "Content-Type: application/json" \
     -d '{
       "topic": "Python Programming",
       "difficulty": "medium",
       "quiz_count": 5
     }'

# Check health
curl http://localhost:8001/health

# Generate focused quiz on specific categories
curl -X POST "http://localhost:8001/generate-quiz" \
     -H "Content-Type: application/json" \
     -d '{
       "topic": "Web Development",
       "difficulty": "hard",
       "quiz_count": 6,
       "focus_categories": ["Frontend", "Backend"]
     }'
```

## Quiz Structure

Each question in a generated quiz has:

- **question**: The question text with category marker [Category: name]
- **options**: Array of 4 possible answers
- **answer**: The correct answer choice
- **explanation**: Explanation of why the answer is correct
- **category**: The topic category for the question

## Category Distribution

The API automatically:
1. Assigns questions to categories
2. Ensures each category has at least 2 questions (except for odd-numbered quizzes)
3. Respects focus categories when specified
4. Maintains equal distribution when multiple focus categories are selected

## Performance Tracking

After evaluating answers, the API provides:
- Overall score percentage
- Category-wise performance
- Detailed question-by-question results
- Individual explanations

## Session Reporting

Generate PDF reports that include:
- Session summary (topics covered, difficulties)
- Aggregated performance by category
- Overall statistics

## Error Handling

The API returns appropriate HTTP status codes:
- `200`: Success
- `400`: Bad request (invalid parameters)
- `500`: Internal server error (AI API failures)

Error responses include a detail message explaining the issue.

## Testing

Run the comprehensive test suite:

```bash
python test_quiz_api.py
```

This tests:
- Health check
- Quiz generation
- Answer evaluation
- Focused quiz generation

## Development & Debugging

Run with auto-reload in development:

```bash
uvicorn quiz_fastapi_app:app --reload --host 0.0.0.0 --port 8001
```

## Environment Variables

- `GEMINI_API_KEY`: Your Google Gemini API key (required)

## Supported Languages

- English
- Kannada
- Hindi
- (Other languages supported by Gemini AI)

## Difficulty Levels

- `easy`: Basic concepts
- `medium`: Intermediate understanding  
- `hard`: Advanced/tricky questions

## Dependencies

- FastAPI: Web framework
- Uvicorn: ASGI server
- Pydantic: Data validation
- Google Generative AI: Quiz generation
- ReportLab: PDF generation

## Limitations

- Quiz generation depends on Gemini AI availability
- Category distribution is a best-effort approach
- Maximum recommended questions per quiz: 50
- API response time depends on Gemini AI latency

## Support

For issues or questions, check:
1. Gemini API key is properly set
2. API server is running
3. Network connectivity to Google AI services
4. Test script output for detailed error messages