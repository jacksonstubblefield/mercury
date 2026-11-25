import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "../api";

/** Image types Claude accepts. Anything else is converted. */
const ACCEPTED = new Set(["image/png", "image/jpeg", "image/gif", "image/webp"]);
/** The API takes at most 5 MB of base64 per image (3.75 MB of bytes); matches the backend's limit. */
const MAX_BYTES = (5 * 1024 * 1024 * 3) / 4;
const MAX_IMAGES = 10;

let nextId = 0;

export interface PastedImage {
  id: string;
  /** data: URL, for the preview */
  url: string;
  /** base64 payload sent to the backend */
  data: string;
}

function readDataUrl(blob: Blob): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(reader.result as string);
    reader.onerror = () => reject(reader.error);
    reader.readAsDataURL(blob);
  });
}

/** Big screenshots (4K, multiple monitors) can pass the API's limit: shrink them until they fit. */
async function fitForClaude(file: Blob): Promise<Blob> {
  if (ACCEPTED.has(file.type) && file.size <= MAX_BYTES) return file;
  let bitmap: ImageBitmap;
  try {
    bitmap = await createImageBitmap(file);
  } catch {
    throw new Error("Couldn't read that image. Paste a PNG, JPEG, GIF or WebP.");
  }
  try {
    const canvas = document.createElement("canvas");
    let scale = 1;
    for (let attempt = 0; attempt < 8; attempt++) {
      canvas.width = Math.max(1, Math.round(bitmap.width * scale));
      canvas.height = Math.max(1, Math.round(bitmap.height * scale));
      canvas.getContext("2d")!.drawImage(bitmap, 0, 0, canvas.width, canvas.height);
      // WebP keeps screenshot text crisp at a fraction of PNG's size.
      const blob = await new Promise<Blob | null>((resolve) => canvas.toBlob(resolve, "image/webp", 0.9));
      if (blob && blob.size <= MAX_BYTES) return blob;
      scale *= 0.75;
    }
  } finally {
    bitmap.close();
  }
  throw new Error("That image is too large to send, even shrunk.");
}

function imageFiles(data: DataTransfer): File[] {
  return [...data.files].filter((f) => f.type.startsWith("image/"));
}

/**
 * Screenshots the user pastes or drops into a composer. Spread `handlers` onto the textarea
 * (or a wrapper), show `images` with <ImageStrip>, and send `payload` with the message.
 */
export function usePastedImages() {
  const [images, setImages] = useState<PastedImage[]>([]);
  const [error, setError] = useState<string | null>(null);
  // The latest list, for checks between awaits (state may not have re-rendered yet).
  const current = useRef<PastedImage[]>([]);
  const update = useCallback((next: PastedImage[]) => {
    current.current = next;
    setImages(next);
  }, []);

  const add = useCallback(
    async (files: File[]) => {
      setError(null);
      for (const file of files) {
        if (current.current.length >= MAX_IMAGES) {
          setError(`You can attach up to ${MAX_IMAGES} images to one message.`);
          return;
        }
        try {
          const url = await readDataUrl(await fitForClaude(file));
          update([...current.current, { id: `img-${nextId++}`, url, data: url.slice(url.indexOf(",") + 1) }]);
        } catch (e) {
          setError(e instanceof Error ? e.message : String(e));
        }
      }
    },
    [update],
  );

  const handlers = {
    onPaste: (e: React.ClipboardEvent) => {
      const files = imageFiles(e.clipboardData);
      // Text wins when the clipboard has both (e.g. cells copied from a spreadsheet come with a picture).
      if (!files.length || e.clipboardData.types.includes("text/plain")) return;
      e.preventDefault();
      void add(files);
    },
    onDragOver: (e: React.DragEvent) => {
      if (e.dataTransfer.types.includes("Files")) e.preventDefault();
    },
    onDrop: (e: React.DragEvent) => {
      const files = imageFiles(e.dataTransfer);
      if (!files.length) return;
      e.preventDefault();
      void add(files);
    },
  };

  return {
    images,
    error,
    handlers,
    payload: images.map((i) => i.data),
    remove: (id: string) => update(current.current.filter((i) => i.id !== id)),
    clear: () => {
      update([]);
      setError(null);
    },
  };
}

/** Thumbnails of the images waiting to be sent, each removable. */
export function ImageStrip({ images, onRemove }: { images: PastedImage[]; onRemove: (id: string) => void }) {
  const [open, setOpen] = useState<string | null>(null);
  if (!images.length) return null;
  return (
    <div className="image-strip">
      {images.map((img) => (
        <div key={img.id} className="image-thumb">
          <img src={img.url} alt="Pasted image" onClick={() => setOpen(img.url)} />
          <button className="image-remove" title="Remove" aria-label="Remove image" onClick={() => onRemove(img.id)}>
            ×
          </button>
        </div>
      ))}
      {open && <Lightbox src={open} onClose={() => setOpen(null)} />}
    </div>
  );
}

/** The images sent with a message, in the transcript. */
export function MessageImages({ names }: { names: string[] }) {
  const [open, setOpen] = useState<string | null>(null);
  if (!names.length) return null;
  return (
    <div className="image-strip">
      {names.map((name) => (
        <div key={name} className="image-thumb">
          <img src={api.attachmentUrl(name)} alt="Attached image" loading="lazy" onClick={() => setOpen(api.attachmentUrl(name))} />
        </div>
      ))}
      {open && <Lightbox src={open} onClose={() => setOpen(null)} />}
    </div>
  );
}

function Lightbox({ src, onClose }: { src: string; onClose: () => void }) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);
  return (
    <div className="modal-backdrop lightbox" onClick={onClose}>
      <img src={src} alt="" />
    </div>
  );
}
