  import React from 'react';

// Component for the selection toolbox
interface SelectionToolboxProps {
  position: { top: number; left: number } | null;
  onExplain: () => void;
  onClose: () => void;
}

const SelectionToolbox: React.FC<SelectionToolboxProps> = ({ position, onExplain, onClose }) => {
  if (!position) return null;

  // Dynamically set top/left for positioning
  const dynamicStyle = {
    top: `${position.top}px`,
    left: `${position.left}px`,
  };

  return (
    <div
      id="pdf-selection-toolbox" // Add specific ID
      className="selection-toolbox" // Keep class for potential fallback/other uses
      style={dynamicStyle}
    >
      <button onClick={onExplain} className="toolbox-button explain-button" title="Explain Selection">
        ✨
      </button>
      <button onClick={onClose} className="toolbox-button close-button" title="Close Toolbox">
        ❌
      </button>
    </div>
  );
};

export default SelectionToolbox;