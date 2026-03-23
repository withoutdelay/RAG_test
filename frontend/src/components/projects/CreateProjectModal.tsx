'use client';

import { useState } from 'react';
import { useRouter } from 'next/navigation';
import { Button } from '@/components/ui/button';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Textarea } from '@/components/ui/textarea';
import api, { getApiErrorMessage } from '@/lib/api';
import { toast } from 'sonner';

export function CreateProjectModal({ children }: { children?: React.ReactElement }) {
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const [loading, setLoading] = useState(false);
  const [formData, setFormData] = useState({
    name: '',
    industry: '',
    product_line: '',
    description: '',
  });

  const handleChange = (e: React.ChangeEvent<HTMLInputElement | HTMLTextAreaElement>) => {
    setFormData((prev) => ({ ...prev, [e.target.name]: e.target.value }));
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!formData.name) {
      toast.error('Project name is required');
      return;
    }

    setLoading(true);
    try {
      const res = await api.post('/projects', formData);
      toast.success('Project created successfully');
      setOpen(false);
      // redirect or refresh
      router.push(`/projects/${res.data.id}`);
    } catch (error) {
      toast.error(getApiErrorMessage(error, 'Failed to create project'));
    } finally {
      setLoading(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger render={children || <Button>Create Project</Button>} />
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>Create New Project</DialogTitle>
          <DialogDescription>
            Enter the details of the RFP or presale task to create a new intelligent workspace.
          </DialogDescription>
        </DialogHeader>
        <form onSubmit={handleSubmit}>
          <div className="grid gap-4 py-4">
            <div className="grid gap-2">
              <Label htmlFor="name">Project Name <span className="text-red-500">*</span></Label>
              <Input
                id="name"
                name="name"
                placeholder="e.g. 2026 Core Banking Migration"
                value={formData.name}
                onChange={handleChange}
                disabled={loading}
              />
            </div>
            <div className="grid grid-cols-2 gap-4">
              <div className="grid gap-2">
                <Label htmlFor="industry">Industry</Label>
                <Input
                  id="industry"
                  name="industry"
                  placeholder="e.g. Finance"
                  value={formData.industry}
                  onChange={handleChange}
                  disabled={loading}
                />
              </div>
              <div className="grid gap-2">
                <Label htmlFor="product_line">Product Line</Label>
                <Input
                  id="product_line"
                  name="product_line"
                  placeholder="e.g. Cloud Infrastructure"
                  value={formData.product_line}
                  onChange={handleChange}
                  disabled={loading}
                />
              </div>
            </div>
            <div className="grid gap-2">
              <Label htmlFor="description">Short Description</Label>
              <Textarea
                id="description"
                name="description"
                placeholder="Briefly describe the requirements..."
                value={formData.description}
                onChange={handleChange}
                disabled={loading}
                rows={3}
              />
            </div>
          </div>
          <DialogFooter>
            <Button type="button" variant="outline" onClick={() => setOpen(false)} disabled={loading}>
              Cancel
            </Button>
            <Button type="submit" disabled={loading}>
              {loading ? 'Creating...' : 'Create Project'}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
