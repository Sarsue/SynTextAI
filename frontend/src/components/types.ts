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

export type ProcessingStatus =
  | 'uploaded'
  | 'extracting'
  | 'embedding'
  | 'storing'
  | 'generating_concepts'
  | 'processed'
  | 'failed';

export interface UploadedFile {
    id: number;
    file_name: string;
    file_url: string;
    created_at?: string;
    file_type: 'pdf' | 'image' | 'audio' | 'video' | 'text' | 'youtube';
    status: ProcessingStatus; // Current processing state

}

export interface KeyConcept {
    id: number;
    file_id: number;
    concept_title: string | null;
    concept_explanation: string | null;
    display_order: number | null;
    source_page_number: number | null;
    source_video_timestamp_start_seconds: number | null;
    source_video_timestamp_end_seconds: number | null;
    created_at: string;
    is_custom: boolean;
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
    key_concept_id: number | null;
    question: string;
    answer: string;
    is_custom: boolean;
    created_at?: string;
}

export interface QuizQuestion {
    id: number;
    file_id: number;
    key_concept_id: number | null;
    question: string;
    question_type: 'MCQ' | 'TF';
    correct_answer: string;
    distractors: string[];
    is_custom: boolean;
    created_at?: string;
}

export interface PaginationState {
    page: number;
    pageSize: number;
    totalItems: number;
}