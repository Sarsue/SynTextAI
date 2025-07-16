import React, { useState, useEffect } from 'react';
import { Flashcard } from './types';
import './FlashcardViewer.css';

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
  
  // Track review status for each card
  const [reviewStatus, setReviewStatus] = useState<{ [id: number]: 'known' | 'needs_review' | 'unseen' }>(
    () => Object.fromEntries(flashcards.map(fc => [fc.id, 'unseen']))
  );

  const total = flashcards.length;
  const reviewed = Object.values(reviewStatus).filter(s => s !== 'unseen').length;
  
  // Update review status when flashcards prop changes
  useEffect(() => {
    setReviewStatus(prev => {
      const newStatus = { ...prev };
      // Add any new flashcards that aren't in the status yet
      flashcards.forEach(fc => {
        if (!(fc.id in newStatus)) {
          newStatus[fc.id] = 'unseen';
        }
      });
      return newStatus;
    });
  }, [flashcards]);

  const handleFlip = () => setFlipped(f => !f);
  const handleNext = () => {
    setFlipped(false);
    setCurrent(prevCurrent => {
      const next = prevCurrent + 1 < total ? prevCurrent + 1 : prevCurrent;
      // Update review status for the current card if it's being viewed for the first time
      if (next !== prevCurrent && card && reviewStatus[card.id] === 'unseen') {
        setReviewStatus(prev => ({ ...prev, [card.id]: 'unseen' }));
      }
      return next;
    });
  };

  const handlePrev = () => {
    setFlipped(false);
    setCurrent(prevCurrent => {
      const prev = prevCurrent - 1 >= 0 ? prevCurrent - 1 : prevCurrent;
      // Update review status for the current card if it's being viewed for the first time
      if (prev !== prevCurrent && card && reviewStatus[card.id] === 'unseen') {
        setReviewStatus(prev => ({ ...prev, [card.id]: 'unseen' }));
      }
      return prev;
    });
  };
  const markCard = (status: 'known' | 'needs_review') => {
    if (flashcards[current]) {
      const currentCardId = flashcards[current].id;
      setReviewStatus(prev => ({
        ...prev,
        [currentCardId]: status
      }));
      
      // If this was the last card, don't auto-advance
      if (current < flashcards.length - 1) {
        handleNext();
      }
    }
  };

  if (!flashcards.length) return <div>No flashcards available.</div>;

  const card = flashcards[current];
  const status = card ? reviewStatus[card.id] || 'unseen' : 'unseen';

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

  const currentCard = flashcards[current];
  const currentStatus = currentCard ? reviewStatus[currentCard.id] || 'unseen' : 'unseen';

  return (
    <div className="flashcard-viewer">
      <div className="progress-indicator">
        <span className="card-position">Card {current + 1} of {total}</span>
        <span className="divider">•</span>
        <span className="reviewed-count">
          <span className="reviewed-number">{reviewed}</span> reviewed
          {total > 0 && (
            <span className="reviewed-percentage">
              ({Math.round((reviewed / total) * 100)}%)
            </span>
          )}
        </span>
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
              <button className="action-btn edit-btn" onClick={handleEdit}>
                <span className="icon">✏️</span>
                <span className="text">Edit</span>
              </button>
              <button className="action-btn delete-btn" onClick={handleDelete}>
                <span className="icon">🗑️</span>
                <span className="text">Delete</span>
              </button>
            </div>
          </>
        )}
      </div>
    </div>
  );
};

export default FlashcardViewer;
