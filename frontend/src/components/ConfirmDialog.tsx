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
                {/* Titles carry file names, and a file name has no spaces to
                    wrap at: "Delete
                    irs_publication_334_tax_guide_small_business.pdf?" ran 49px
                    outside a panel 304px wide.

                    max-w-full is the part that matters, and it is not obvious.
                    These are grid children, so the box is sized to its content
                    and never learns the panel's width; overflow-wrap alone
                    changed nothing because there was no constraint to wrap
                    against. Measured in the browser at 1280x800, overflow past
                    the panel edge:

                        as shipped                        +49px
                        break-words only                  +49px
                        max-w-full                        -15px

                    overflow-wrap:anywhere stays so the break can happen mid-word
                    once the width is constrained, since a file name offers
                    nowhere else to break. */}
                <AlertDialogTitle className="max-w-full [overflow-wrap:anywhere]">
                    {title}
                </AlertDialogTitle>
                {description && (
                    <AlertDialogDescription className="max-w-full [overflow-wrap:anywhere]">
                        {description}
                    </AlertDialogDescription>
                )}
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
