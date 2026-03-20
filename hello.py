import tkinter as tk


def show_greeting() -> None:
    name = name_var.get().strip()
    if not name:
        greeting_var.set("Please enter your name.")
        return

    greeting_var.set(f"Hello, {name}!")


root = tk.Tk()
root.title("Greeting App")
root.geometry("320x180")
root.resizable(False, False)

name_var = tk.StringVar()
greeting_var = tk.StringVar(value="Enter your name and click Greet.")

frame = tk.Frame(root, padx=20, pady=20)
frame.pack(fill="both", expand=True)

title_label = tk.Label(frame, text="What's your name?", font=("Helvetica", 14))
title_label.pack(pady=(0, 12))

name_entry = tk.Entry(frame, textvariable=name_var, width=28)
name_entry.pack(pady=(0, 12))
name_entry.focus()

greet_button = tk.Button(frame, text="Greet", command=show_greeting, width=12)
greet_button.pack(pady=(0, 12))

greeting_label = tk.Label(frame, textvariable=greeting_var, wraplength=260)
greeting_label.pack()

root.bind("<Return>", lambda event: show_greeting())
root.mainloop()
