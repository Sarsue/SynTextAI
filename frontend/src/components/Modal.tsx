import React from 'react';
import './Modal.css'; // We'll create this next

interface ModalProps {
  isOpen: boolean;
  onClose: () => void;
  title: string;
  children: React.ReactNode;
  darkMode: boolean;
}

const Modal: React.FC<ModalProps> = ({ isOpen, onClose, title, children, darkMode }) => {
  if (!isOpen) return null;

  return (
    // The overlay div covers the whole screen and closes the modal when clicked
    <div className="modal-overlay" onClick={onClose}>
      <div 
        // The content div is the actual modal window
        className={`modal-content ${darkMode ? 'dark-mode' : ''}`}
        // Stop clicks inside the modal from bubbling up and closing it
        onClick={(e) => e.stopPropagation()} 
      >
        <div className="modal-header">
          <h2>{title}</h2>
          <button onClick={onClose} className="modal-close-button" aria-label="Close modal">✕</button>
        </div>
        <div className="modal-body">
          {children}
        </div>
      </div>
    </div>
  );
};

export default Modal;