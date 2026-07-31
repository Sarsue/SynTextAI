import React from 'react';
import {
    AlertDialog,
    AlertDialogAction,
    AlertDialogCancel,
    AlertDialogContent,
    AlertDialogDescription,
    AlertDialogFooter,
    AlertDialogHeader,
    AlertDialogTitle,
} from '@/components/ui/alert-dialog';

interface ConfirmDialogProps {
    open: boolean;
    title: string;
    description?: string;
    confirmLabel?: string;
    cancelLabel?: string;
    /** Styles the confirm action as destructive. Deletes should set this. */
    destructive?: boolean;
    onConfirm: () => void;
    onCancel: () => void;
}

/**
 * One confirmation dialog for the whole app.
 *
 * Replaces window.confirm, which rendered an OS chrome dialog with the page's
 * origin in it, ignored dark mode entirely, and blocked the main thread. It
 * also cannot be styled or tested. Written once here so the three call sites
 * that needed it do not each grow their own AlertDialog boilerplate.
 */
const ConfirmDialog: React.FC<ConfirmDialogProps> = ({
    open,
    title,
    description,
    confirmLabel = 'Confirm',
    cancelLabel = 'Cancel',
    destructive = false,
    onConfirm,
    onCancel,
}) => (
    <AlertDialog open={open} onOpenChange={(next) => { if (!next) onCancel(); }}>
        <AlertDialogContent>
            <AlertDialogHeader>
                <AlertDialogTitle>{title}</AlertDialogTitle>
                {description && <AlertDialogDescription>{description}</AlertDialogDescription>}
            </AlertDialogHeader>
            <AlertDialogFooter>
                <AlertDialogCancel onClick={onCancel}>{cancelLabel}</AlertDialogCancel>
                <AlertDialogAction
                    onClick={onConfirm}
                    className={destructive ? 'bg-destructive text-white hover:bg-destructive/90' : undefined}
                >
                    {confirmLabel}
                </AlertDialogAction>
            </AlertDialogFooter>
        </AlertDialogContent>
    </AlertDialog>
);

export default ConfirmDialog;
