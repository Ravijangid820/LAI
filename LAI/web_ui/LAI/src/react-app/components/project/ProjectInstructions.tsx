import { useState } from "react";
import { Pencil } from "lucide-react";
import { Button } from "@/react-app/components/ui/button";
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
} from "@/react-app/components/ui/card";

interface ProjectInstructionsProps {
  instructions: string;
  onSave: (text: string) => void;
}

export function ProjectInstructions({
  instructions,
  onSave,
}: ProjectInstructionsProps) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(instructions);

  const handleSave = () => {
    onSave(draft);
    setEditing(false);
  };

  const handleEdit = () => {
    setDraft(instructions);
    setEditing(true);
  };

  return (
    <Card className="bg-card/50 backdrop-blur border-border/50">
      <CardHeader className="pb-2 flex flex-row items-center justify-between">
        <CardTitle className="text-sm font-semibold">Instructions</CardTitle>
        {!editing && (
          <button
            onClick={handleEdit}
            className="text-muted-foreground hover:text-foreground transition-colors"
          >
            <Pencil className="w-3.5 h-3.5" />
          </button>
        )}
      </CardHeader>
      <CardContent>
        {editing ? (
          <div className="space-y-2">
            <textarea
              className="w-full text-xs text-muted-foreground bg-background/50 rounded-md border border-border/50 p-2 resize-none outline-none focus:border-primary/50 transition-colors"
              rows={4}
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              autoFocus
            />
            <div className="flex gap-2">
              <Button
                size="sm"
                className="text-xs h-7 px-3"
                onClick={handleSave}
              >
                Save
              </Button>
              <Button
                size="sm"
                variant="ghost"
                className="text-xs h-7 px-3"
                onClick={() => setEditing(false)}
              >
                Cancel
              </Button>
            </div>
          </div>
        ) : (
          <p className="text-xs text-muted-foreground leading-relaxed">
            {instructions ||
              "No instructions added yet. Click the edit icon to add."}
          </p>
        )}
      </CardContent>
    </Card>
  );
}
