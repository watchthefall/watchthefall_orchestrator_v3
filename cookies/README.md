# Cookie Files

This directory is used to store Instagram cookie files for authenticated downloads.

## How to Add Cookies

1. Export your Instagram cookies in Netscape format
2. Save the file with the naming pattern: `cookies_*.txt` (e.g., `cookies_account1.txt`)
3. The system will automatically validate and use the first valid cookie file found

## Cookie File Requirements

- Must be in Netscape HTTP Cookie File format
- Must contain the following cookies:
  - `ig_did`
  - `csrftoken`
  - `sessionid`
  - `ds_user_id`

## Security Notes

- Cookie files are excluded from Git via `.gitignore`
- Cookie names and contents are never exposed in logs or UI
- Files are only accessed by the backend fetch logic

## Valid Cookie File Example

```
# Netscape HTTP Cookie File
.instagram.com	TRUE	/	TRUE	0	ig_did	XXXXXXXXXXXXXX
.instagram.com	TRUE	/	TRUE	0	csrftoken	XXXXXXXXXXXXXX
.instagram.com	TRUE	/	TRUE	0	sessionid	XXXXXXXXXXXXXX
.instagram.com	TRUE	/	TRUE	0	ds_user_id	XXXXXXXXXXXXXX
```