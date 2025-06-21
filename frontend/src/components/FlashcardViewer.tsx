import React, { useState } from 'react';
import './FlashcardViewer.css';

interface Flashcard {
  id: number;
  file_id: number;
  key_concept_id: number;
  question: string;
  answer: string;
  is_custom: boolean;
  status?: 'unseen' | 'known' | 'needs_review';
}

interface FlashcardViewerProps {
  flashcards: Flashcard[];
  onUpdateFlashcard: (id: number, data: { question: string; answer: string }) => void;
  onDeleteFlashcard: (id: number) => void;
}

const FlashcardViewer: React.FC<FlashcardViewerProps> = ({ flashcards, onUpdateFlashcard, onDeleteFlashcard }) => {
  const [current, setCurrent] = useState(0);
  const [flipped, setFlipped] = useState(false);
  const [isEditing, setIsEditing] = useState(false);
  const [editedQuestion, setEditedQuestion] = useState('');
  const [editedAnswer, setEditedAnswer] = useState('');
  const [cardStatus, setCardStatus] = useState<{ [id: number]: 'known' | 'needs_review' | 'unseen' }>(
    Object.fromEntries(flashcards.map(fc => [fc.id, 'unseen']))
  );

  const total = flashcards.length;
  const reviewed = Object.values(cardStatus).filter(s => s !== 'unseen').length;

  const handleFlip = () => setFlipped(f => !f);
  const handleNext = () => {
    setFlipped(false);
    setCurrent(i => (i + 1 < total ? i + 1 : i));
  };
  const handlePrev = () => {
    setFlipped(false);
    setCurrent(i => (i - 1 >= 0 ? i - 1 : i));
  };
  const markCard = (status: 'known' | 'needs_review') => {
    setCardStatus(s => ({ ...s, [flashcards[current].id]: status }));
    handleNext();
  };

  if (!flashcards.length) return <div>No flashcards available.</div>;

  const card = flashcards[current];
  const status = card ? cardStatus[card.id] : 'unseen';

  const handleEdit = () => {
    if (!card) return;
    setEditedQuestion(card.question);
    setEditedAnswer(card.answer);
    setIsEditing(true);
    setFlipped(false); // Exit flip mode when editing
  };

  const handleCancel = () => {
    setIsEditing(false);
  };

  const handleSave = () => {
    if (!card) return;
    onUpdateFlashcard(card.id, { question: editedQuestion, answer: editedAnswer });
    setIsEditing(false);
  };

  const handleDelete = () => {
    if (!card) return;
    if (window.confirm('Are you sure you want to delete this flashcard?')) {
      onDeleteFlashcard(card.id);
      if (current >= flashcards.length - 1) {
        setCurrent(Math.max(0, flashcards.length - 2));
      }
    }
  };

  return (
    <div className="flashcard-viewer">
      <div className="progress-indicator">
        {reviewed}/{total} cards reviewed
      </div>

      {isEditing ? (
        <div className="flashcard-editor">
          <div className="edit-field">
            <label>Question</label>
            <textarea value={editedQuestion} onChange={(e) => setEditedQuestion(e.target.value)} />
          </div>
          <div className="edit-field">
            <label>Answer</label>
            <textarea value={editedAnswer} onChange={(e) => setEditedAnswer(e.target.value)} />
          </div>
        </div>
      ) : (
        <div className={`flashcard${flipped ? ' flipped' : ''}`} onClick={!isEditing ? handleFlip : undefined}>
          <div className="flashcard-inner">
            <div className="flashcard-front">{card.question}</div>
            <div className="flashcard-back">{card.answer}</div>
          </div>
        </div>
      )}

      <div className="navigation">
        <button onClick={handlePrev} disabled={current === 0 || isEditing} className="icon-btn">⬅️</button>
        <button onClick={handleNext} disabled={current === total - 1 || isEditing} className="icon-btn">➡️</button>
      </div>

      <div className="actions">
        {isEditing ? (
          <>
            <button onClick={handleSave}>Save</button>
            <button onClick={handleCancel}>Cancel</button>
          </>
        ) : (
          <>
            <button onClick={() => markCard('known')} disabled={status === 'known'} className="icon-btn">✅</button>
            <button onClick={() => markCard('needs_review')} disabled={status === 'needs_review'} className="icon-btn">🤔</button>
            <div className="custom-actions">
              <button className="icon-btn edit-btn" onClick={handleEdit}>✏️</button>
              <button className="icon-btn delete-btn" onClick={handleDelete}>🗑️</button>
            </div>
          </>
        )}
      </div>
    </div>
  );
};

export default FlashcardViewer;
