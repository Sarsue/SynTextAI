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
}

const FlashcardViewer: React.FC<FlashcardViewerProps> = ({ flashcards }) => {
  const [current, setCurrent] = useState(0);
  const [flipped, setFlipped] = useState(false);
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
  const status = cardStatus[card.id];

  return (
    <div className="flashcard-viewer">
      <div className="progress-indicator">
        {reviewed}/{total} cards reviewed
      </div>
      <div className={`flashcard${flipped ? ' flipped' : ''}`} onClick={handleFlip}>
        <div className="flashcard-inner">
          <div className="flashcard-front">{card.question}</div>
          <div className="flashcard-back">{card.answer}</div>
        </div>
      </div>
      <div className="navigation">
        <button onClick={handlePrev} disabled={current === 0}>Previous</button>
        <button onClick={handleNext} disabled={current === total - 1}>Next</button>
      </div>
      <div className="actions">
        <button onClick={() => markCard('known')} disabled={status === 'known'}>Mark as Known</button>
        <button onClick={() => markCard('needs_review')} disabled={status === 'needs_review'}>Needs Review</button>
      </div>
    </div>
  );
};

export default FlashcardViewer;
