import React, { useState } from 'react';
import './QuizInterface.css';
import { QuizQuestion } from './types';

interface QuizInterfaceProps {
  questions: QuizQuestion[];
  onUpdateQuiz: (id: number, data: Partial<QuizQuestion>) => void;
  onDeleteQuiz: (id: number) => void;
}

const QuizInterface: React.FC<QuizInterfaceProps> = ({ questions, onUpdateQuiz, onDeleteQuiz }) => {
  const [current, setCurrent] = useState(0);
  const [selected, setSelected] = useState<string | null>(null);
  const [score, setScore] = useState(0);
  const [answered, setAnswered] = useState<{ [id: number]: boolean }>({});
  const [showFeedback, setShowFeedback] = useState(false);
  const [isCorrect, setIsCorrect] = useState<boolean | null>(null);
  const [isEditing, setIsEditing] = useState(false);
  const [editedQuestion, setEditedQuestion] = useState<QuizQuestion | null>(null);

  const total = questions.length;
  const progress = Object.keys(answered).length;

  if (!questions.length) return <div>No quiz questions available.</div>;

  const q = questions[current];
  const options = q ? (q.question_type === 'MCQ' ? [...q.distractors, q.correct_answer].sort() : ['True', 'False']) : [];

  const handleEdit = () => {
    if (!q) return;
    setEditedQuestion({ ...q });
    setIsEditing(true);
  };

  const handleCancel = () => {
    setIsEditing(false);
    setEditedQuestion(null);
  };

  const handleSave = () => {
    if (!editedQuestion) return;
    onUpdateQuiz(editedQuestion.id, {
        question: editedQuestion.question,
        correct_answer: editedQuestion.correct_answer,
        distractors: editedQuestion.distractors,
    });
    setIsEditing(false);
    setEditedQuestion(null);
  };

  const handleDelete = () => {
    if (!q) return;
    if (window.confirm('Are you sure you want to delete this quiz question?')) {
        onDeleteQuiz(q.id);
        if (current >= questions.length - 1) {
            setCurrent(Math.max(0, questions.length - 2));
        }
    }
  };

  const handleSelect = (option: string) => {
    if (answered[q.id]) return;
    setSelected(option);
  };
  const handleSubmit = () => {
    if (selected == null || answered[q.id]) return;
    const correct = selected === q.correct_answer;
    setIsCorrect(correct);
    setShowFeedback(true);
    setAnswered(a => ({ ...a, [q.id]: true }));
    if (correct) setScore(s => s + 1);
  };
  const handleNext = () => {
    setShowFeedback(false);
    setSelected(null);
    setIsCorrect(null);
    setCurrent(i => (i + 1 < total ? i + 1 : i));
  };
  const handlePrev = () => {
    setShowFeedback(false);
    setSelected(null);
    setIsCorrect(null);
    setCurrent(i => (i - 1 >= 0 ? i - 1 : i));
  };
  const handleRestart = () => {
    setShowFeedback(false);
    setSelected(null);
    setIsCorrect(null);
    setCurrent(0);
    setScore(0);
    setAnswered({});
  };

  if (isEditing && editedQuestion) {
    return (
      <div className="quiz-editor">
        <h3>Edit Quiz Question</h3>
        <div className="edit-field">
            <label>Question</label>
            <textarea 
                value={editedQuestion.question}
                onChange={e => setEditedQuestion({...editedQuestion, question: e.target.value})}
            />
        </div>
        <div className="edit-field">
            <label>Correct Answer</label>
            <input 
                type="text"
                value={editedQuestion.correct_answer}
                onChange={e => setEditedQuestion({...editedQuestion, correct_answer: e.target.value})}
            />
        </div>
        <div className="edit-field">
            <label>Distractors (comma-separated)</label>
            <input 
                type="text"
                value={editedQuestion.distractors.join(',')}
                onChange={e => setEditedQuestion({...editedQuestion, distractors: e.target.value.split(',')})}
            />
        </div>
        <div className="actions">
          <button onClick={handleSave}>Save</button>
          <button onClick={handleCancel}>Cancel</button>
        </div>
      </div>
    )
  }

  return (
    <div className="quiz-interface">
       {q && !isEditing && (
        <div className="custom-actions">
            <button className="edit-btn" onClick={handleEdit}>Edit</button>
            <button className="delete-btn" onClick={handleDelete}>Delete</button>
        </div>
      )}
      <div className="progress-indicator">
        {progress}/{total} questions answered | Score: {score}
      </div>
      <div className="quiz-question">
        <div className="question-text">{q.question}</div>
        <div className="options">
          {options.map(option => (
            <button
              key={option}
              className={`option-btn${selected === option ? ' selected' : ''}`}
              onClick={() => handleSelect(option)}
              disabled={!!answered[q.id] || showFeedback}
            >
              {option}
            </button>
          ))}
        </div>
      </div>
      {!answered[q.id] && (
        <button onClick={handleSubmit} disabled={selected == null}>Submit</button>
      )}
      {showFeedback && (
        <div className={`feedback ${isCorrect ? 'correct' : 'incorrect'}`}>
          {isCorrect ? 'Correct!' : `Incorrect. Correct answer: ${q.correct_answer}`}
        </div>
      )}
      <div className="navigation">
        <button onClick={handlePrev} disabled={current === 0}>Previous</button>
        <button onClick={handleNext} disabled={current === total - 1}>Next</button>
      </div>
      <div className="actions">
        <button onClick={handleRestart}>Restart Quiz</button>
      </div>
    </div>
  );
};

export default QuizInterface;
