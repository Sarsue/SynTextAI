import React from 'react';
import { History } from './types';
import './HistoryView.css';

interface HistoryViewProps {
    histories: History[];
    setCurrentHistory: (historyId: number) => void;
    onClearHistory: () => void;
    onNewChat: () => void;
    onDeleteHistory: (historyId: number | History) => void;
    onDownloadHistory: () => void
}

const HistoryView: React.FC<HistoryViewProps> = ({ histories, setCurrentHistory, onClearHistory, onNewChat, onDeleteHistory, onDownloadHistory }) => {
    const [selectedHistoryId, setSelectedHistoryId] = React.useState<number | null>(null);

    const onSelectHistory = (history: History) => {
        setCurrentHistory(history.id);
        setSelectedHistoryId(history.id);
    };

    return (

        <div className="history-container">
            <div className="history-header">
                <button className="history-button" onClick={onClearHistory}>
                    🗑️
                </button>
                <button className="history-button" onClick={onNewChat}>
                    ➕
                </button>
                <button className="history-button" onClick={onDownloadHistory}>
                    ⬇️
                </button>
            </div>
            {histories.slice().reverse().map((history) => (
                <div
                    key={history.id}
                    onClick={() => onSelectHistory(history)}
                    className={`history-item ${selectedHistoryId === history.id ? 'selected' : ''}`}
                    style={{ backgroundColor: selectedHistoryId === history.id ? '#e0e0e0' : 'transparent' }}
                >
                    <span className="history-content">{history.messages.length > 0 ? history.messages[0].content : 'No messages'}</span>
                    <button
                        className="delete-button"
                        onClick={(e) => {
                            e.stopPropagation();

                            // Show confirmation dialog
                            const isConfirmed = window.confirm('Are you sure you want to delete this history?');

                            // Check if the user clicked "OK"
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
    );
};

export default HistoryView;
