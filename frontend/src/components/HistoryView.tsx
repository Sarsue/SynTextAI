import React from 'react';
import { History } from './types';
import './HistoryView.css';
import { useDarkMode } from '../DarkModeContext';

interface HistoryViewProps {
    histories: History[];
    setCurrentHistory: (historyId: number) => void;
    onClearHistory: () => void;
    onNewChat: () => void;
    onDeleteHistory: (historyId: number | History) => void;
    onDownloadHistory: () => void;
}

const HistoryView: React.FC<HistoryViewProps> = ({
    histories,
    setCurrentHistory,
    onClearHistory,
    onNewChat,
    onDeleteHistory,
    onDownloadHistory,
}) => {
    const [selectedHistoryId, setSelectedHistoryId] = React.useState<number | null>(null);
    const { darkMode } = useDarkMode(); // Access the darkMode state

    const onSelectHistory = (history: History) => {
        setCurrentHistory(history.id);
        setSelectedHistoryId(history.id);
    };

    return (
        <div className={`history-container ${darkMode ? 'dark-mode' : ''}`}>
            <h3>📜</h3>
            {/* Action button always visible at the top */}
            <div className="history-actions">
                <button className="history-action" onClick={onNewChat}>✚</button>
            </div>

            {/* History list */}
            <div className="history-list">
                {histories.slice().reverse().map((history) => (
                    <div
                        key={history.id}
                        onClick={() => onSelectHistory(history)}
                        className={`history-item ${selectedHistoryId === history.id ? 'selected' : ''}`}
                    >
                        <span className="history-content">
                            {history.messages.length > 0 ? history.messages[0].content : 'No messages'}
                        </span>
                        <button
                            className="delete-button"
                            onClick={(e) => {
                                e.stopPropagation();
                                const isConfirmed = window.confirm('Are you sure you want to delete this history?');
                                if (isConfirmed) {
                                    onDeleteHistory(history);
                                }
                            }}
                        >
                            X
                        </button>
                    </div>
                ))}
            </div>

            {/* Save and Clear buttons at the bottom */}
            <div className="history-actions bottom-actions">
                <button className="history-action" onClick={onDownloadHistory}>⬇️</button>
                <button className="history-action" onClick={onClearHistory}>🗑️</button>
            </div>
        </div>

    );
};

export default HistoryView;
