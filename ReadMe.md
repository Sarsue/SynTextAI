
To Do
Fix text overflow in learning ux/ui, adjust text to conttol and control to screen

fix the quality of answers and concepts 

* Search within documents
    * Simple keyword-based retrieval + context display.
* Simple UI
    * Web app with drag-and-drop or paste input.
    * Gamify dashboard: Summaries, Flashcards, Quizzes tabs.
* Enterprise version on Azure 



Improvements
Add Customization Options:
Allow users to specify quiz difficulty (e.g., number of options, question types).
Add parameters for comprehension level in quiz generation functions.

Agentic way of doing things evolution from worker and tasks. ingestion agent, learning repo agent, we can add more agents

Simplified Worker Polling:
Worker now checks for files every 30 seconds with a fixed interval instead of exponential backoff for simpler and more predictable processing.

Testing

CMDS
uvicorn api.app:app --reload --host 0.0.0.0 --port 3000
docker-compose -f docker-compose.yml -f docker-compose.local.yml up --build && docker-compose logs -f