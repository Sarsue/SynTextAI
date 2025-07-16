import React, { useState, useEffect } from 'react';
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
  const progressPercentage = total > 0 ? Math.round((progress / total) * 100) : 0;

  if (!questions.length) return <div>No quiz questions available.</div>;

  const q = questions[current];
  const options = q ? (q.question_type === 'MCQ' ? Array.from(new Set([...q.distractors, q.correct_answer])).sort() : ['True', 'False']) : [];

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
    setCurrent(prevCurrent => {
      const next = prevCurrent + 1 < total ? prevCurrent + 1 : prevCurrent;
      return next;
    });
  };

  const handlePrev = () => {
    setShowFeedback(false);
    setSelected(null);
    setIsCorrect(null);
    setCurrent(prevCurrent => {
      const prev = prevCurrent - 1 >= 0 ? prevCurrent - 1 : prevCurrent;
      return prev;
    });
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
      <div className="progress-indicator">
        <span className="position">Question {current + 1} of {total}</span>
        <span className="divider">•</span>
        <span className="progress">
          <span className="progress-number">{progress}</span> answered
          {total > 0 && (
            <span className="progress-percentage">({progressPercentage}%)</span>
          )}
        </span>
        <span className="divider">•</span>
        <span className="score">
          Score: <span className="score-number">{score}</span>/{progress || '0'}
        </span>
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
      <div className="actions">
        {!answered[q.id] && (
          <button onClick={handleSubmit} disabled={!selected} className="icon-btn">📤</button>
        )}
        <button onClick={handleRestart} className="icon-btn restart-btn">🔄</button>
        {q && !isEditing && (
          <div className="custom-actions">
            <button className="action-btn edit-btn" onClick={handleEdit}>
              <span className="icon">✏️</span>
              <span className="text">Edit</span>
            </button>
            <button className="action-btn delete-btn" onClick={handleDelete}>
              <span className="icon">🗑️</span>
              <span className="text">Delete</span>
            </button>
          </div>
        )}
      </div>

      {showFeedback && (
        <div className={`feedback ${isCorrect ? 'correct' : 'incorrect'}`}>
          {isCorrect ? 'Correct!' : `Incorrect. Correct answer: ${q.correct_answer}`}
        </div>
      )}

      <div className="navigation">
        <button onClick={handlePrev} disabled={current === 0} className="icon-btn">⬅️</button>
        <button onClick={handleNext} disabled={current === total - 1} className="icon-btn">➡️</button>
      </div>
    </div>
  );
};

export default QuizInterface;
