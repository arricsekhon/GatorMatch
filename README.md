# GatorMatch — Peer Tutoring Platform

> A full-stack web application connecting SFSU students with peer tutors for
> in-person and online tutoring sessions.

Built for **CSC 648 – Software Engineering** at San Francisco State University,
Fall 2025. Developed by a 7-person Agile team across 5 milestones.

---

## Features

- **Tutor Discovery** — Search and filter tutors by course, subject, and
  location with 4 sort modes (best match, highest rated, most experienced)
- **Session Booking** — Students request sessions with tutors; tutors
  approve, deny, or cancel with full status tracking
- **Video Meetings** — Automatic Jitsi Meet link generation for online sessions
- **Messaging** — Threaded inbox/sent messaging between students and tutors
- **Role-Based Access** — Three user roles: Student, Tutor, and Admin
- **Tutor Applications** — Students apply to become tutors; admins review
  and approve/reject through a dedicated dashboard
- **Availability Scheduling** — Tutors set weekly availability blocks that
  appear on their booking form
- **Dashboards** — Separate dashboards for tutors and students showing
  pending requests, upcoming sessions, session history, and messages
- **Profile Management** — Users edit their profile; tutors upload avatars
  and update bio/headline
- **Google Analytics** — Integrated for usage tracking

---

## Tech Stack

| Layer | Technology |
|---|---|
| Backend | Python 3, Flask |
| ORM | SQLAlchemy 2.0 |
| Database | SQLite (dev) / MySQL (production) |
| Auth | Flask-Login, Werkzeug password hashing |
| Forms & CSRF | Flask-WTF |
| Templates | Jinja2 |
| Production Server | Gunicorn |
| Video | Jitsi Meet API |
| Analytics | Google Analytics |
| Frontend | HTML5, CSS3, vanilla JS |

---
