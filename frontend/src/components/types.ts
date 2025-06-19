export interface DocumentInfo {
    id: number;
    title: string;
    type: 'pdf' | 'image' | 'audio' | 'video' | 'text';
    path: string;
}

export interface User {
    uid: string;
    displayName: string | null;
    email: string | null;

}

export type ProcessingStatus = 'uploaded' | 'processing' | 'extracted' | 'processed' | 'failed';

export interface UploadedFile {
    id: number;
    name: string;
    publicUrl: string;
    upload_time?: string;
    type: 'pdf' | 'image' | 'audio' | 'video' | 'text' | 'youtube';
    size?: number; // File size in bytes
    viewStartTime?: number; // Timestamp when file was opened for viewing
    processing_status: ProcessingStatus; // Current processing state
    error_message?: string | null; // Error message if processing failed
}

export interface KeyConcept {
    id: number;
    file_id: number;
    concept_title?: string | null;
    concept_explanation?: string | null;
    display_order?: number | null;
    source_page_number?: number | null;
    source_video_timestamp_start_seconds?: number | null;
    source_video_timestamp_end_seconds?: number | null;
    created_at: string;
    is_custom?: boolean;
}

export interface Message {

    id: number;

    content: string;

    sender: 'user' | 'bot';

    timestamp: string;

    liked: boolean;

    disliked: boolean;

}

export interface History {
    id: number;
    title: string;
    messages: Message[];
}

export interface Persona {
    id: number;
    name: string;
}

export interface Flashcard {
    id: number;
    file_id: number;
    key_concept_id: number;
    question: string;
    answer: string;
    is_custom: boolean;
    status?: 'unseen' | 'known' | 'needs_review';
}

export interface QuizQuestion {
    id: number;
    file_id: number;
    key_concept_id: number;
    question: string;
    question_type: 'MCQ' | 'TF';
    correct_answer: string;
    distractors: string[];
}
