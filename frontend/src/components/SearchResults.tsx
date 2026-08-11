import React, { useState } from 'react';
import { FileText, Search as SearchIcon } from 'lucide-react';

import { useUserContext } from '../UserContext';
import FileViewerComponent from './FileViewerComponent';
import { UploadedFile } from './types';
import './SearchResults.css';

export interface SearchHit {
    file_id: number;
    file_name: string;
    page_number: number | null;
    snippet: string;
    passages: number;
}

interface SearchResultsProps {
    query: string;
    results: SearchHit[];
    isSearching: boolean;
    hasSearched: boolean;
    files: UploadedFile[];
}

/**
 * What was found, not what to think about it.
 *
 * Deliberately plain. A result is a page: the document it is in, the page
 * number, and the passage that matched. Nothing is generated here and nothing
 * is scored on screen, because the whole reason somebody chooses this over
 * chat is that they want to read the source and decide for themselves.
 */
const SearchResults: React.FC<SearchResultsProps> = ({
    query,
    results,
    isSearching,
    hasSearched,
    files,
}) => {
    const { darkMode, user } = useUserContext();
    const [openFile, setOpenFile] = useState<UploadedFile | null>(null);
    const [openFragment, setOpenFragment] = useState<string>('');
    const [openError, setOpenError] = useState<string | null>(null);

    /**
     * Documents are private in storage, so a result carries the document's
     * identity and never a readable link. The signed URL is minted at the
     * moment of opening, exactly as a citation does it, which is what makes
     * losing access to a workspace stop working immediately.
     */
    const open = async (hit: SearchHit) => {
        if (!user) return;
        setOpenError(null);
        try {
            const token = await user.getIdToken();
            const response = await fetch(`api/v1/files/${hit.file_id}/access-url`, {
                headers: { Authorization: `Bearer ${token}` },
                mode: 'cors',
                credentials: 'include',
            });
            if (!response.ok) {
                setOpenError(`Could not open ${hit.file_name}. You may no longer have access to it.`);
                return;
            }
            const data = await response.json();
            if (typeof data?.url !== 'string') {
                setOpenError(`Could not open ${hit.file_name}.`);
                return;
            }

            const known = files.find((f) => f.id === hit.file_id);
            setOpenFile({
                ...(known ?? ({} as UploadedFile)),
                id: hit.file_id,
                file_name: hit.file_name,
                file_url: data.url,
            } as UploadedFile);
            setOpenFragment(hit.page_number ? `#page=${hit.page_number}` : '');
        } catch {
            setOpenError(`Could not open ${hit.file_name}.`);
        }
    };

    /**
     * The searched words, marked in the passage.
     *
     * Split on the terms rather than replacing into HTML, so nothing from a
     * document is ever handed to dangerouslySetInnerHTML. A page of a customer's
     * PDF is not trusted markup.
     */
    const highlight = (text: string) => {
        const terms = query
            .toLowerCase()
            .split(/\s+/)
            .filter((t) => t.length > 2)
            .map((t) => t.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'));
        if (terms.length === 0) return text;

        const pattern = new RegExp(`(${terms.join('|')})`, 'ig');
        return text.split(pattern).map((part, i) =>
            terms.includes(part.toLowerCase()) ? <mark key={i}>{part}</mark> : part,
        );
    };

    if (isSearching) {
        return (
            <div className={`search-results ${darkMode ? 'dark-mode' : ''}`}>
                <p className="search-status">Searching…</p>
            </div>
        );
    }

    if (!hasSearched) {
        return (
            <div className={`search-results ${darkMode ? 'dark-mode' : ''}`}>
                <div className="search-empty">
                    <SearchIcon className="search-empty-icon" aria-hidden="true" />
                    <p className="search-empty-title">Find a passage</p>
                    <p className="search-empty-body">
                        Search your documents and go straight to the page. No answer is
                        written, so nothing is invented.
                    </p>
                </div>
            </div>
        );
    }

    if (results.length === 0) {
        return (
            <div className={`search-results ${darkMode ? 'dark-mode' : ''}`}>
                <div className="search-empty">
                    <p className="search-empty-title">Nothing matched “{query}”</p>
                    <p className="search-empty-body">
                        Try fewer words, or the words you would expect to see written in
                        the document.
                    </p>
                </div>
            </div>
        );
    }

    return (
        <div className={`search-results ${darkMode ? 'dark-mode' : ''}`}>
            <p className="search-status">
                {results.length} {results.length === 1 ? 'page' : 'pages'} for “{query}”
            </p>

            {openError && <p className="search-error">{openError}</p>}

            <ul className="search-hits">
                {results.map((hit) => (
                    <li key={`${hit.file_id}-${hit.page_number}`}>
                        <button
                            type="button"
                            className="search-hit"
                            onClick={() => open(hit)}
                            aria-label={`Open ${hit.file_name}${hit.page_number ? `, page ${hit.page_number}` : ''}`}
                        >
                            <span className="search-hit-head">
                                <FileText className="search-hit-icon" aria-hidden="true" />
                                <span className="search-hit-name">{hit.file_name}</span>
                                {hit.page_number != null && (
                                    <span className="search-hit-page">Page {hit.page_number}</span>
                                )}
                                {hit.passages > 1 && (
                                    <span className="search-hit-passages">
                                        {hit.passages} passages
                                    </span>
                                )}
                            </span>
                            <span className="search-hit-snippet">{highlight(hit.snippet)}</span>
                        </button>
                    </li>
                ))}
            </ul>

            {openFile && (
                <FileViewerComponent
                    file={openFile}
                    fragment={openFragment}
                    onClose={() => setOpenFile(null)}
                    onError={(e) => setOpenError(e)}
                    darkMode={darkMode}
                />
            )}
        </div>
    );
};

export default SearchResults;
