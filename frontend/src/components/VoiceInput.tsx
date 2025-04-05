import React, { useState, useEffect, useRef, useCallback } from 'react';
import { checkBrowserSupport, filterProfanity } from '../utils/voiceUtils';
import './VoiceInput.css';

const MicrophoneIcon = () => (
    <svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
        <path d="M12 2a3 3 0 0 0-3 3v7a3 3 0 0 0 6 0V5a3 3 0 0 0-3-3z" />
        <path d="M19 10v2a7 7 0 0 1-14 0v-2" />
        <line x1="12" y1="19" x2="12" y2="22" />
    </svg>
);

const MicrophoneActiveIcon = () => (
    <svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="currentColor">
        <path d="M12 2a3 3 0 0 0-3 3v7a3 3 0 0 0 6 0V5a3 3 0 0 0-3-3z" />
        <path d="M19 10v2a7 7 0 0 1-14 0v-2" />
        <line x1="12" y1="19" x2="12" y2="22" stroke="currentColor" strokeWidth="2" />
    </svg>
);

interface VoiceInputProps {
    onTranscript: (text: string) => void;
    darkMode: boolean;
}

const VoiceInput: React.FC<VoiceInputProps> = ({
    onTranscript,
    darkMode
}) => {
    const [isListening, setIsListening] = useState(false);
    const [recognition, setRecognition] = useState<SpeechRecognition | null>(null);
    const [interimResult, setInterimResult] = useState('');
    const [transcript, setTranscript] = useState('');
    const [error, setError] = useState<string | null>(null);
    const transcriptHistoryRef = useRef<string[]>([]);

    useEffect(() => {
        const support = checkBrowserSupport();
        if (!support.supported) {
            setError(support.reason || 'Speech recognition not supported');
        }
    }, []);

    const initializeSpeechRecognition = useCallback(() => {
        if ('SpeechRecognition' in window || 'webkitSpeechRecognition' in window) {
            const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
            const recognitionInstance = new SpeechRecognition();

            recognitionInstance.continuous = true;
            recognitionInstance.interimResults = true;

            recognitionInstance.onstart = () => {
                setError(null);
                setIsListening(true);
            };

            recognitionInstance.onend = () => {
                setIsListening(false);
            };

            recognitionInstance.onerror = function (this: SpeechRecognition, ev: Event) {
                const event = ev as SpeechRecognitionErrorEvent;
                console.error('Speech recognition error:', event.error);

                const errorMessages: { [key: string]: string } = {
                    'not-allowed': 'Microphone access denied. Please allow microphone access.',
                    'network': 'Network error. Please check your connection.',
                    'no-speech': 'No speech was detected.',
                    'audio-capture': 'No microphone was found.',
                    'service-not-allowed': 'Speech recognition service is not allowed.'
                };

                const errorMessage = errorMessages[event.error] || `Speech recognition error: ${event.error}`;
                setError(errorMessage);
                setIsListening(false);
            };

            recognitionInstance.onresult = (event: SpeechRecognitionEvent) => {
                const results = Array.from(event.results);
                const lastResult = results[results.length - 1];

                if (lastResult.isFinal) {
                    let finalTranscript = lastResult[0].transcript.trim();
                    finalTranscript = filterProfanity(finalTranscript);

                    console.log('Final Transcript:', finalTranscript);
                    
                    // Always pass the full transcript to onTranscript
                    onTranscript(finalTranscript);
                    
                    // Reset interim result
                    setInterimResult('');
                } else {
                    // Update interim result for UI feedback
                    setInterimResult(lastResult[0].transcript);
                }
            };

            setRecognition(recognitionInstance);
        } else {
            setError('Speech recognition is not supported in your browser.');
        }
    }, [onTranscript]);

    useEffect(() => {
        initializeSpeechRecognition();
    }, [initializeSpeechRecognition]);

    const startListening = () => {
        if (!recognition) return;

        try {
            recognition.start();
            setIsListening(true);
            setError(null);
        } catch (err) {
            console.warn('Recognition already started or failed to start:', err);
        }
    };

    const stopListening = () => {
        if (!recognition) return;

        try {
            recognition.stop();
        } catch (err) {
            console.warn('Error stopping recognition:', err);
        } finally {
            setIsListening(false);
            setInterimResult('');
        }
    };

    const toggleListening = () => {
        if (isListening) {
            stopListening();
        } else {
            startListening();
        }
    };

    return (
        <div className="voice-input-container">
            <button
                className={`voice-input-button ${isListening ? 'listening' : ''} ${error ? 'disabled' : ''}`}
                onClick={toggleListening}
                disabled={!!error}
                aria-label="Voice input"
            >
                {isListening ? <MicrophoneActiveIcon /> : <MicrophoneIcon />}
            </button>
            {interimResult && <div className="interim-result">{interimResult}</div>}
            {error && <div className="voice-input-error">{error}</div>}
        </div>
    );
};

export default VoiceInput;
