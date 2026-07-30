"use client";
import { useState, useRef } from "react";
import { Image, Send, X } from "lucide-react";

export default function CreatePost({ onPostCreated }) {
  const [content, setContent] = useState("");
  const [selectedFile, setSelectedFile] = useState(null);
  const [previewUrl, setPreviewUrl] = useState(null);
  const [loading, setLoading] = useState(false);
  const fileInputRef = useRef(null);

  const handleFileChange = (e) => {
    const file = e.target.files[0];
    if (file) {
      setSelectedFile(file);
      setPreviewUrl(URL.createObjectURL(file));
    }
  };

  const removeImage = () => {
    setSelectedFile(null);
    setPreviewUrl(null);
    if (fileInputRef.current) fileInputRef.current.value = "";
  };

  const handleSubmit = async (e) => {
    e.preventDefault();
    if (!content.trim() && !selectedFile) return;

    setLoading(true);
    const formData = new FormData();
    if (content) formData.append("content", content);
    if (selectedFile) formData.append("file", selectedFile);

    try {
      const token = localStorage.getItem("access_token");
      const res = await fetch("http://localhost:8000/api/posts/create", {
        method: "POST",
        headers: {
          Authorization: `Bearer ${token}`,
        },
        body: formData,
      });

      if (res.ok) {
        const newPost = await res.json();
        setContent("");
        removeImage();
        if (onPostCreated) onPostCreated(newPost);
      } else {
        alert("Failed to create post");
      }
    } catch (err) {
      console.error("Error creating post:", err);
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="bg-slate-900 border border-slate-800 rounded-xl p-4 mb-6 shadow-md">
      <form onSubmit={handleSubmit}>
        <textarea
          value={content}
          onChange={(e) => setContent(e.target.value)}
          placeholder="What's on your mind?"
          className="w-full bg-slate-800 text-white placeholder-slate-400 p-3 rounded-lg focus:outline-none focus:ring-2 focus:ring-indigo-500 resize-none h-24"
        />

        {previewUrl && (
          <div className="relative mt-2 inline-block">
            <img
              src={previewUrl}
              alt="Preview"
              className="h-32 rounded-lg object-cover border border-slate-700"
            />
            <button
              type="button"
              onClick={removeImage}
              className="absolute -top-2 -right-2 bg-red-600 text-white rounded-full p-1 hover:bg-red-700 transition"
            >
              <X size={14} />
            </button>
          </div>
        )}

        <div className="flex items-center justify-between mt-3 pt-3 border-t border-slate-800">
          <input
            type="file"
            accept="image/*"
            ref={fileInputRef}
            onChange={handleFileChange}
            className="hidden"
          />
          <button
            type="button"
            onClick={() => fileInputRef.current?.click()}
            className="flex items-center gap-2 text-slate-400 hover:text-indigo-400 text-sm transition"
          >
            <Image size={18} />
            <span>Add Photo</span>
          </button>

          <button
            type="submit"
            disabled={loading || (!content.trim() && !selectedFile)}
            className="flex items-center gap-2 bg-indigo-600 hover:bg-indigo-500 disabled:opacity-50 text-white px-4 py-2 rounded-lg text-sm font-medium transition"
          >
            <Send size={16} />
            <span>{loading ? "Posting..." : "Post"}</span>
          </button>
        </div>
      </form>
    </div>
  );
}