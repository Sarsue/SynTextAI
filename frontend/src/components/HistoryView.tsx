import React from 'react';
import { Plus, X } from 'lucide-react';
import { History } from './types';
import './HistoryView.css';
import { useUserContext } from '../UserContext';
import { Button } from '@/components/ui/button';
import ConfirmDialog from './ConfirmDialog';

interface HistoryViewProps {
    histories: History[];
    setCurrentHistory: (historyId: number) => void;
    onNewChat: () => void;
    onDeleteHistory: (historyId: number | History) => void;
}

const HistoryView: React.FC<HistoryViewProps> = ({
    histories,
    setCurrentHistory,
    onNewChat,
    onDeleteHistory,
}) => {
    const [selectedHistoryId, setSelectedHistoryId] = React.useState<number | null>(null);
    const [pendingDelete, setPendingDelete] = React.useState<History | null>(null);
    const { darkMode } = useUserContext(); // Access the darkMode state

    const onSelectHistory = (history: History) => {
        setCurrentHistory(history.id);
        setSelectedHistoryId(history.id);
    };

    return (
        <div className={`history-container ${darkMode ? 'dark-mode' : ''}`}>
            <h3>📜</h3>
            {/* Action button always visible at the top */}
            <div className="history-actions">
                <Button onClick={onNewChat} className="w-full">
                    <Plus className="size-4" /> New chat
                </Button>
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
                            {/* The list endpoint returns a title and a preview,
                                not the messages, which are fetched only when a
                                conversation is opened. Keying the label off
                                messages[0] therefore labelled every row "No
                                messages". The title is the question that started
                                the conversation, which is what identifies it. */}
                            {history.messages.length > 0
                                ? history.messages[0].content.slice(0, 140) + (history.messages[0].content.length > 140 ? '...' : '')
                                : (history.title || 'New chat')}
                        </span>
                        <Button
                            variant="ghost"
                            size="icon-sm"
                            className="delete-button shrink-0"
                            onClick={(e) => {
                                e.stopPropagation();
                                setPendingDelete(history);
                            }}
                        >
                            <X className="size-4" />
                        </Button>
                    </div>
                ))}
            </div>

            <ConfirmDialog
                open={pendingDelete !== null}
                title="Delete this conversation?"
                description="The conversation and its answers will be removed. This cannot be undone."
                confirmLabel="Delete"
                destructive
                onConfirm={() => {
                    if (pendingDelete) onDeleteHistory(pendingDelete);
                    setPendingDelete(null);
                }}
                onCancel={() => setPendingDelete(null)}
            />
        </div>

    );
};

export default React.memo(HistoryView);
