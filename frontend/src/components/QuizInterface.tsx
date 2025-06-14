import React, { useState } from 'react';
import './QuizInterface.css';

interface QuizQuestion {
  id: number;
  file_id: number;
  key_concept_id: number;
  question: string;
  question_type: 'MCQ' | 'TF';
  correct_answer: string;
  distractors: string[];
}

interface QuizInterfaceProps {
  questions: QuizQuestion[];
}

const QuizInterface: React.FC<QuizInterfaceProps> = ({ questions }) => {
  const [current, setCurrent] = useState(0);
  const [selected, setSelected] = useState<string | null>(null);
  const [score, setScore] = useState(0);
  const [answered, setAnswered] = useState<{ [id: number]: boolean }>({});
  const [showFeedback, setShowFeedback] = useState(false);
  const [isCorrect, setIsCorrect] = useState<boolean | null>(null);

  const total = questions.length;
  const progress = Object.keys(answered).length;

  if (!questions.length) return <div>No quiz questions available.</div>;

  const q = questions[current];
  const options = q.question_type === 'MCQ' ? [...q.distractors, q.correct_answer].sort() : ['True', 'False'];

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

  return (
    <div className="quiz-interface">
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
