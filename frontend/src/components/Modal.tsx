import React from 'react';
import './Modal.css'; // We'll create this next

interface ModalProps {
  isOpen: boolean;
  onClose: () => void;
  title: string;
  children: React.ReactNode;
  darkMode: boolean;
  onDownload?: () => void; // Optional download handler
}

const Modal: React.FC<ModalProps> = ({ isOpen, onClose, title, children, darkMode, onDownload }) => {
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
        {/* Add Modal Footer with Download Button */}
        <div className="modal-footer">
          {onDownload && (
            <button onClick={onDownload} className="modal-download-button">
              Download Summary (.txt)
            </button>
          )}
          <button onClick={onClose} className="modal-footer-close-button">
            Close
          </button>
        </div>
      </div>
    </div>
  );
};

export default Modal;