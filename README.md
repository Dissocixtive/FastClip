### RUSSIAN
#  InstantReplay – Мгновенные повторы экрана и микрофона

Программа постоянно записывает экран и звук с микрофона в фоне, позволяя одним нажатием сохранить последние секунды происходящего в видеофайл. Удобно для гейминга, стримов, записи созвонов и любых моментов, которые хочется запечатлеть постфактум.

##  Возможности

-  **Буферизация последних секунд** (настраивается от 5 до 120 секунд)
-  **Запись с микрофона** с выбором устройства и регулировкой громкости
-  **Гибкие настройки**:
  - FPS (15/30/60)
  - Качество видео (низкое/среднее/высокое – влияет на битрейт)
  - Путь сохранения файлов
  - Горячие клавиши (по умолчанию `Ctrl+S` – сохранить повтор, `Ctrl+V` – показать оверлей)
-  **Компактный оверлей** (вызывается по хоткею или из системного трея) с кнопками управления
-  **Встроенный видеоплеер** для просмотра сохранённых повторов
-  **Современный фиолетово‑серый дизайн** с анимациями и эффектами прозрачности
- **Работа из системного трея** – не мешает работе, всегда под рукой

##  Быстрый старт

### 1. Установка зависимостей

Для запуска исходного кода (Python 3.8+)

```bash
pip install numpy pyaudio PyQt5 pynput mss moviepy
```

### 2. Запуск

```bash
python FastClip.py
```

> [!WARNING]
> Программа может потреблять до 10 гб RAM при средних настройках (30 fps |  medium)


### ENGLISH

# InstantReplay – Instant screen and microphone replays 

The program constantly records the screen and sound from the microphone in the background, allowing you to save the last seconds of what is happening to a video file with a single tap. It is convenient for gaming, streaming, recording calls and any moments that you want to capture after the fact. 
## Features 

- **Buffering of the last seconds** (configurable from 5 to 120 seconds) 
- **Microphone recording** with device selection and volume control - **Flexible settings**: - FPS (15/30/60) 
- Video quality (low/medium/high – affects bitrate) 
- File saving path - Keyboard shortcuts (by default, `Ctrl+S` – save repeat, `Ctrl+V` – show overlay) 
- **Compact overlay** (accessed via a hotspot or from the system tray) with control buttons - **Built-in video player** for viewing saved replays 
- **Modern purple‑gray design** with animations and transparency effects 
- **Work from the system tray** – does not interfere with work, always at hand 
## Quick start 

### 1. Installing dependencies 

To run the source code (Python 3.8+) 

```bash 
pip install numpy pyaudio PyQt5 pynput mss moviepy 
``` 
### 2. Launch

```bash 
python FastClip.py 
``` 

> [!WARNING] 
> The program can consume up to 10 GB of RAM at medium settings (30 fps | medium)