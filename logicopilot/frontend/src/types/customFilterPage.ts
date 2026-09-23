export interface CustomFilterPage {
  id: string;
  name: string;
  original_filename: string | null;
  page_count: number;
  is_active: boolean;
  uploaded_by: string | null;
  created_at: string;
  updated_at: string;
}
