import { useState } from "react";

interface Props {
  title: string;
  app: string;
  /** Apps already in use, offered as suggestions rather than a fixed list. */
  knownApps: string[];
  editing: boolean;
  onEdit: () => void;
  onCancel: () => void;
  onSave: (title: string, app: string) => void;
}

/**
 * The document's name and which product it belongs to.
 *
 * The app is free text with suggestions rather than a dropdown of existing
 * values: the first document for a new product has to be able to name it, and
 * a dropdown that cannot express "something new" would make the common case
 * the impossible one.
 */
export function DocumentName({
  title,
  app,
  knownApps,
  editing,
  onEdit,
  onCancel,
  onSave,
}: Props) {
  const [draftTitle, setDraftTitle] = useState(title);
  const [draftApp, setDraftApp] = useState(app);

  function begin() {
    setDraftTitle(title);
    setDraftApp(app);
    onEdit();
  }

  if (!editing) {
    return (
      <div className="docname">
        {app && <span className="app-chip">{app}</span>}
        <h2>{title}</h2>
        <button className="rename" onClick={begin}>
          Rename
        </button>
      </div>
    );
  }

  return (
    <form
      className="docname-edit"
      onSubmit={(e) => {
        e.preventDefault();
        onSave(draftTitle, draftApp);
      }}
    >
      <label>
        <span>Website or app</span>
        {/* A distinct id from the sidebar's list: two datalists sharing one
            would be duplicate ids, and the browser would silently bind both
            inputs to whichever came first. */}
        <input
          value={draftApp}
          list="known-apps-rename"
          placeholder="e.g. LeetCode"
          onChange={(e) => setDraftApp(e.target.value)}
        />
        <datalist id="known-apps-rename">
          {knownApps.map((a) => (
            <option key={a} value={a} />
          ))}
        </datalist>
      </label>
      <label>
        <span>Procedure</span>
        <input
          value={draftTitle}
          placeholder="e.g. Log in"
          autoFocus
          onChange={(e) => setDraftTitle(e.target.value)}
        />
      </label>
      <div className="docname-actions">
        <button type="submit" disabled={!draftTitle.trim()}>
          Save
        </button>
        <button type="button" onClick={onCancel}>
          Cancel
        </button>
      </div>
    </form>
  );
}
