import { initializeApp } from 'firebase/app';
import { getAuth, GoogleAuthProvider } from 'firebase/auth';


// Your web app's Firebase configuration
const firebaseConfig = {
    apiKey: "AIzaSyCLpucZwHHxh_6O0UDXEwSVuEiAWQrbnNc",
    authDomain: "docsynth-fbb02.firebaseapp.com",
    projectId: "docsynth-fbb02",
    storageBucket: "docsynth-fbb02.appspot.com",
    messagingSenderId: "109840175801",
    appId: "1:109840175801:web:3f2b1ac45882e4e76c0168"
};

// Initialize Firebase


const app = initializeApp(firebaseConfig);
const auth = getAuth(app);
const googleProvider = new GoogleAuthProvider();

export { auth, googleProvider };
