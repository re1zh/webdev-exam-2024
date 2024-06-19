'use strict';

function modalShown(event) {
    let button = event.relatedTarget;
    let bookId = button.getAttribute('data-book-id');
    let bookName = button.getAttribute('data-book-name');
    let newUrl = `/books/${bookId}/delete_book`;
    let form = document.getElementById('deleteModalBookForm');
    let modalBookName = document.getElementById('modalBookName');

    form.action = newUrl;
    modalBookName.textContent = bookName;
}

let modal = document.getElementById('deleteModalBook');
modal.addEventListener('show.bs.modal', modalShown);